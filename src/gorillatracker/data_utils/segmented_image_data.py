from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import numpy as np


@dataclass
class SegmentedImageData:
    path: str
    width: int
    height: int
    segments: Dict[str, List[Tuple[np.ndarray, Tuple[int, int, int, int]]]] = field(default_factory=dict)

    def add_segment(self, class_label: str, mask: np.ndarray, box: Tuple[int, int, int, int]):
        if class_label not in self.segments:
            self.segments[class_label] = []
        self.segments[class_label].append((mask, box))

    