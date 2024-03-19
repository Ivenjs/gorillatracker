import logging
import random
from collections import deque
from dataclasses import dataclass, field
from itertools import groupby
from pathlib import Path
from typing import Iterator

import cv2
from sqlalchemy import Select, select
from sqlalchemy.orm import Session, sessionmaker

from gorillatracker.ssl_pipeline.dataset import GorillaDataset
from gorillatracker.ssl_pipeline.helpers import BoundingBox, video_reader
from gorillatracker.ssl_pipeline.models import TrackingFrameFeature, Video
from gorillatracker.ssl_pipeline.queries import load_video_by_filename

log = logging.getLogger(__name__)


class Sampler:
    """Defines how to sample TrackingFrameFeature instances from the database."""

    def __init__(self, query: Select[tuple[TrackingFrameFeature]]) -> None:
        self.query = query

    def sample(self, video: Path, session: Session) -> Iterator[TrackingFrameFeature]:
        """Sample a subset of TrackingFrameFeature instances from the database. Defined by query and sampling strategy."""
        return iter(session.execute(self.query).scalars().all())

    def group_by_tracking_id(self, frame_features: list[TrackingFrameFeature]) -> dict[int, list[TrackingFrameFeature]]:
        frame_features.sort(key=lambda x: x.tracking.tracking_id)
        return {
            tracking_id: list(features)
            for tracking_id, features in groupby(frame_features, key=lambda x: x.tracking.tracking_id)
        }


class RandomSampler(Sampler):
    """Randomly sample a subset of TrackingFrameFeature instances per tracking."""

    def __init__(self, query: Select[tuple[TrackingFrameFeature]], n_samples: int, seed: int = 42) -> None:
        super().__init__(query)
        self.seed = seed
        self.n_samples = n_samples

    def sample(self, video: Path, session: Session) -> Iterator[TrackingFrameFeature]:
        tracking_frame_features = list(session.execute(self.query).scalars().all())
        tracking_id_grouped = self.group_by_tracking_id(tracking_frame_features)
        random.seed(self.seed)
        for features in tracking_id_grouped.values():
            num_samples = min(len(features), self.n_samples)
            yield from random.sample(features, num_samples)


@dataclass(frozen=True, order=True)
class CropTask:
    frame_nr: int
    dest: Path
    bounding_box: BoundingBox = field(compare=False)


def destination_path(base_path: Path, feature: TrackingFrameFeature) -> Path:
    return Path(base_path, str(feature.tracking.tracking_id), f"{feature.frame_nr}.png")


def crop_from_video(
    video: Path,
    sampler: Sampler,
    session_cls: sessionmaker,
    dest_base_path: Path,
) -> None:
    with session_cls() as session:
        video_tracking = load_video_by_filename(session, video)
        dest_path = dest_base_path / video_tracking.camera.name / video_tracking.filename
        frame_features = sampler.sample(video, session)
        crop_tasks = [
            CropTask(
                feature.frame_nr, destination_path(dest_path, feature), BoundingBox.from_tracking_frame_feature(feature)
            )
            for feature in frame_features
        ]

    print(len(crop_tasks))
    crop_queue = deque(sorted(crop_tasks))

    if not crop_queue:
        log.warning(f"No frames to crop for video: {video}")
        return

    with video_reader(video) as video_feed:
        for video_frame in video_feed:
            while crop_queue and video_frame.frame_nr == crop_queue[0].frame_nr:
                crop_task = crop_queue.popleft()
                cropped_frame = video_frame.frame[
                    crop_task.bounding_box.y_top_left : crop_task.bounding_box.y_bottom_right,
                    crop_task.bounding_box.x_top_left : crop_task.bounding_box.x_bottom_right,
                ]
                crop_task.dest.parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(crop_task.dest), cropped_frame)
        assert not crop_queue, "Not all crop tasks were completed"


if __name__ == "__main__":
    import shutil

    from sqlalchemy import create_engine
    from tqdm import tqdm

    from gorillatracker.ssl_pipeline.queries import (
        feature_type_filter,
        min_count_filter,
        video_filter,
    )

    engine = create_engine("postgresql+psycopg2://postgres:DEV_PWD_139u02riowenfgiw4y589wthfn@postgres:5432/postgres")

    def sampling_strategy(
        *, video: Path, min_n_images_per_tracking: int
    ) -> Select[tuple[TrackingFrameFeature]]:
        query = video_filter(video)
        query = min_count_filter(query, min_n_images_per_tracking)
        query = feature_type_filter(query, [GorillaDataset.FACE_90, GorillaDataset.FACE_45])
        return query

    shutil.rmtree("cropped_images")

    session_cls = sessionmaker(bind=engine)

    with session_cls() as session:
        video_trackings = session.execute(select(Video)).scalars().all()
        videos = [Path("video_data", video.filename) for video in video_trackings]
        
    random.shuffle(videos)

    for video in tqdm(videos):
        query = sampling_strategy(video=video, min_n_images_per_tracking=200)
        crop_from_video(
            video,
            RandomSampler(query, seed=42, n_samples=10),
            session_cls,
            Path("cropped_images"),
        )
        break

    # from gorillatracker.ssl_pipeline.visualizer import visualize_video
    # p = Path("video_data/R033_20220403_392.mp4")
    # visualize_video(p, session_cls, Path("R033_20220403_392.mp4"))
