from itertools import product
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker
from tqdm import tqdm

from gorillatracker.ssl_pipeline.data_structures import DirectedBipartiteGraph
from gorillatracker.ssl_pipeline.helpers import BoundingBox, groupby_frame
from gorillatracker.ssl_pipeline.models import TrackingFrameFeature, Video
from gorillatracker.ssl_pipeline.queries import load_features, load_tracked_features, load_videos

log = logging.getLogger(__name__)


@dataclass
class Association:
    reference: TrackingFrameFeature
    unassociated: TrackingFrameFeature


class Correlator(Protocol):
    def __call__(
        self,
        tracked_features: list[TrackingFrameFeature],
        untracked_features: list[TrackingFrameFeature],
        threshold: float = 0.7,
    ) -> list[Association]: ...


def build_bipartite_graph(
    tracked_features: list[TrackingFrameFeature],
    untracked_features: list[TrackingFrameFeature],
    threshold: float = 0.7,
) -> DirectedBipartiteGraph[TrackingFrameFeature]:
    graph = DirectedBipartiteGraph(tracked_features, untracked_features)

    tracked_frames = groupby_frame(tracked_features)
    untracked_frames = groupby_frame(untracked_features)

    for frame_nr, tracked_frame_features in tracked_frames.items():
        untracked_frame_features = untracked_frames[frame_nr]
        for tf, uf in product(tracked_frame_features, untracked_frame_features):
            tf_bbox = BoundingBox.from_tracking_frame_feature(tf)
            uf_bbox = BoundingBox.from_tracking_frame_feature(uf)
            if tf_bbox.intersection_over_smallest_area(uf_bbox) > threshold:
                graph.add_edge(tf, uf)

    return graph


def one_to_one_correlator(
    tracked_features: list[TrackingFrameFeature],
    untracked_features: list[TrackingFrameFeature],
    threshold: float = 0.7,
) -> list[Association]:
    graph = build_bipartite_graph(tracked_features, untracked_features, threshold)
    return list(map(lambda a: Association(*a), graph.bijective_relationships()))


def correlate_and_update(
    tracked_features: list[TrackingFrameFeature],
    untracked_features: list[TrackingFrameFeature],
    correlator: Correlator,
    threshold: float = 0.7,
) -> None:
    for untracked_feature in untracked_features:
        untracked_feature.tracking_id = None

    associations = correlator(tracked_features, untracked_features, threshold)
    for association in associations:
        association.unassociated.tracking_id = association.reference.tracking_id


def validate_feature(videos: Sequence[Video], type: str) -> None:
    assert all(
        type in [p.type for p in video.processed_video_frame_features] for video in videos
    ), f"The type {type} must be present in all videos."


def correlate_videos(
    version: str,
    video_paths: list[Path],
    engine: Engine,
    correlator: Correlator,
    untracked_type: str,
    threshold: float = 0.7,
    tracked_type: str = "body",
) -> None:
    session_cls = sessionmaker(bind=engine)
    with session_cls() as session:
        videos = load_videos(session, video_paths, version)
        assert 0 <= threshold <= 1, "Threshold must be between 0 and 1"
        validate_feature(videos, tracked_type)
        validate_feature(videos, untracked_type)

        for video in tqdm(videos, desc="Correlating videos"):
            tracked_features = load_tracked_features(session, video.video_id, [tracked_type])
            untracked_features = load_features(session, video.video_id, [untracked_type])
            correlate_and_update(list(tracked_features), list(untracked_features), correlator, threshold)

        session.commit()
