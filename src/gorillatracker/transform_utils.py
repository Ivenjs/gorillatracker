import torch
from PIL.Image import Image
from torchvision.transforms.functional import pad


class TensorSquarePad(torch.nn.Module):
    def __init__(self, pad_value: int = 0):
        super().__init__()
        self.pad_value = pad_value

    def forward(self, tensor: torch.Tensor) -> torch.Tensor:
        _, h, w = tensor.size()
        max_dim = max(h, w)
        pad_h = max_dim - h
        pad_w = max_dim - w
        pad_top = pad_h // 2
        pad_bottom = pad_h - pad_top
        pad_left = pad_w // 2
        pad_right = pad_w - pad_left
        padded_tensor = pad(tensor, (pad_left, pad_right, pad_top, pad_bottom), value=self.pad_value)
        return padded_tensor


# TODO(memben): Sunset this
class SquarePad:
    def __call__(self, image: Image) -> Image:
        # calc padding
        width, height = image.size
        aspect_ratio = width / height
        if aspect_ratio > 1:
            padding_top = (width - height) // 2
            padding_bottom = width - height - padding_top
            padding = (0, padding_top, 0, padding_bottom)
        else:
            padding_left = (height - width) // 2
            padding_right = height - width - padding_left
            padding = (padding_left, 0, padding_right, 0)

        image = pad(image, padding, (0, 0, 0), "constant")
        return image
