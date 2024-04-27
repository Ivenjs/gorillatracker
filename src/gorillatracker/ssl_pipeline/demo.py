"""
This is a demo file to show how to use the SSL pipeline to track any animal in a video.

The pipeline consists of the following steps:
1. Create a dataset adapter for the dataset of interest. (dataset.py)
2. Extract the metadata from the videos. (video_preprocessor.py)
3. Use a tracking model to track the animal in the video. (video_processor.py) 
    - This should in be a YOLOv8 model (single_cls) trained on the body of the animal of interest.
4. Store the tracking results in a database. (models.py)
5. (Optional) Add additional features to the tracking results and correlate them. (video_processor.py and feature_mapper.py)
6. (Optional) Visualize the tracking results. (visualizer.py)
7. ...
"""

import logging
import random
from pathlib import Path

from sqlalchemy.orm import Session

from gorillatracker.ssl_pipeline.dataset import GorillaDatasetGPUServer2, GorillaDatasetKISZ, SSLDataset
from gorillatracker.ssl_pipeline.feature_mapper import multiprocess_correlate, one_to_one_correlator
from gorillatracker.ssl_pipeline.helpers import remove_processed_videos
from gorillatracker.ssl_pipeline.models import Task, TaskKeyValue, TaskType
from gorillatracker.ssl_pipeline.queries import load_preprocessed_videos, load_videos
from gorillatracker.ssl_pipeline.video_preprocessor import preprocess_videos
from gorillatracker.ssl_pipeline.video_processor import multiprocess_predict, multiprocess_track
from gorillatracker.ssl_pipeline.visualizer import multiprocess_visualize

log = logging.getLogger(__name__)


# TODO(memben): This is a WIP
def create_tasks(session: Session, video_paths: list[Path], version: str) -> None:
    with session.begin():
        videos = load_videos(session, video_paths, version)
        for video in videos:
            for task_type, subtype in [
                (TaskType.TRACK, "body"),
                (TaskType.PREDICT, "face_90"),
                (TaskType.PREDICT, "face_45"),
                (TaskType.VISUALIZE, ""),
            ]:
                video.tasks.append(Task(task_type=task_type, task_subtype=subtype))

            for tracked, untracked, threshold in [
                ("body", "face_90", 0.7),
                ("body", "face_45", 0.7),
            ]:
                task = Task(task_type=TaskType.CORRELATE)
                task.task_key_values.extend(
                    [
                        TaskKeyValue(key="tracked_feature_type", value=tracked),
                        TaskKeyValue(key="untracked_feature_type", value=untracked),
                        TaskKeyValue(key="threshold", value=str(threshold)),
                    ]
                )
                video.tasks.append(task)


def visualize_pipeline(
    dataset: SSLDataset,
    version: str,
    dest_dir: Path,
    n_videos: int = 30,
    target_output_fps: int = 10,
    max_worker_per_gpu: int = 8,
    gpu_ids: list[int] = [0],
) -> None:
    """
    Visualize the tracking results of the pipeline.

    Args:
        dataset (SSLDataset): The dataset to use.
        version (str): The version of the pipeline.
        dest_dir (Path): The destination to save the visualizations.
        n_videos (int, optional): The number of videos to visualize. Defaults to 20.
        target_output_fps (int, optional): The FPS to sample the video at. Defaults to 10.
        max_worker_per_gpu (int, optional): The maximum number of workers per GPU. Defaults to 8.
        gpu_ids (list[int], optional): The GPUs to use for tracking. Defaults to [0].

    Returns:
        None, the visualizations are saved to the destination and to the SSLDataset.
    """

    video_paths = sorted(dataset.video_paths)

    # NOTE(memben): For the production pipeline we should do this for every step
    # owever, in this context, we want the process to fail if not all videos are preprocessed for debugging.
    with Session(dataset.engine) as session:
        preprocessed_videos = list(load_preprocessed_videos(session, version))
        video_paths = remove_processed_videos(video_paths, preprocessed_videos)

    random.seed(42)  # For reproducibility
    videos_to_track = random.sample(video_paths, n_videos)
    max_workers = len(gpu_ids) * max_worker_per_gpu

    preprocess_videos(
        videos_to_track,
        version,
        target_output_fps,
        dataset.engine,
        dataset.metadata_extractor,
        dataset.video_insert_hook,
    )

    create_tasks(session, videos_to_track, version)

    body_model_path, yolo_body_kwargs = dataset.get_yolo_model_config("body")
    multiprocess_track(
        "body",  # NOTE(memben): Tracking will always be done on bodies
        body_model_path,
        yolo_body_kwargs,
        dataset.tracker_config,
        dataset.engine,
        max_worker_per_gpu=max_worker_per_gpu,
        gpu_ids=gpu_ids,
    )

    for feature_type in dataset.features:
        yolo_model, yolo_kwargs = dataset.get_yolo_model_config(feature_type)
        multiprocess_predict(
            feature_type,
            yolo_model,
            yolo_kwargs,
            dataset.engine,
            max_worker_per_gpu=max_worker_per_gpu,
            gpu_ids=gpu_ids,
        )

    multiprocess_correlate(one_to_one_correlator, dataset.engine, max_workers)

    multiprocess_visualize(dest_dir, dataset.engine, max_workers)


def gpu2_demo() -> None:
    version = "2024-04-09"
    logging.basicConfig(level=logging.INFO)
    dataset = GorillaDatasetGPUServer2("sqlite:///test.db")
    # NOTE(memben): for setup only once
    visualize_pipeline(
        dataset,
        version,
        Path("/workspaces/gorillatracker/video_output"),
        n_videos=1,
        max_worker_per_gpu=1,  # NOTE(memben): SQLITE does not support multiprocessing, so we need to set this to 1
        gpu_ids=[0],
    )
    dataset.post_setup()


def kisz_demo() -> None:
    version = "2024-04-09"
    logging.basicConfig(level=logging.INFO)
    dataset = GorillaDatasetKISZ()
    # NOTE(memben): for setup only once
    visualize_pipeline(
        dataset,
        version,
        Path("/workspaces/gorillatracker/video_output"),
        n_videos=20,
        max_worker_per_gpu=10,
        gpu_ids=[0],
    )
    dataset.post_setup()


if __name__ == "__main__":
    gpu2_demo()
