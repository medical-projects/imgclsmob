"""
    SENet, implemented in PyTorch.
    Original paper: 'Squeeze-and-Excitation Networks,' https://arxiv.org/abs/1709.01507.
"""

__all__ = ['SENet', 'senet52', 'senet103', 'senet154']

import os
import math
import torch.nn as nn
import torch.nn.init as init
from .common import SEBlock
from .resnext import resnext_conv3x3, resnext_conv1x1


class SENetBottleneck(nn.Module):
    """
    SENet bottleneck block for residual path in SENet unit.

    Parameters:
    ----------
    in_channels : int
        Number of input channels.
    out_channels : int
        Number of output channels.
    stride : int or tuple/list of 2 int
        Strides of the convolution.
    cardinality: int
        Number of groups.
    bottleneck_width: int
        Width of bottleneck block.
    """
    def __init__(self,
                 in_channels,
                 out_channels,
                 stride,
                 cardinality,
                 bottleneck_width):
        super(SENetBottleneck, self).__init__()
        mid_channels = out_channels // 4
        D = int(math.floor(mid_channels * (bottleneck_width / 64)))
        group_width = cardinality * D
        group_width2 = group_width // 2

        self.conv1 = resnext_conv1x1(
            in_channels=in_channels,
            out_channels=group_width2,
            stride=1,
            activate=True)
        self.conv2 = resnext_conv3x3(
            in_channels=group_width2,
            out_channels=group_width,
            stride=stride,
            groups=cardinality,
            activate=True)
        self.conv3 = resnext_conv1x1(
            in_channels=group_width,
            out_channels=out_channels,
            stride=1,
            activate=False)

    def forward(self, x):
        x = self.conv1(x)
        x = self.conv2(x)
        x = self.conv3(x)
        return x


class SENetUnit(nn.Module):
    """
    SENet unit with residual connection.

    Parameters:
    ----------
    in_channels : int
        Number of input channels.
    out_channels : int
        Number of output channels.
    stride : int or tuple/list of 2 int
        Strides of the convolution.
    cardinality: int
        Number of groups.
    bottleneck_width: int
        Width of bottleneck block.
    identity_conv3x3 : bool, default False
        Whether to use 3x3 convolution in the identity link.
    """
    def __init__(self,
                 in_channels,
                 out_channels,
                 stride,
                 cardinality,
                 bottleneck_width,
                 identity_conv3x3):
        super(SENetUnit, self).__init__()
        self.use_se = True
        self.resize_identity = (in_channels != out_channels) or (stride != 1)

        self.body = SENetBottleneck(
            in_channels=in_channels,
            out_channels=out_channels,
            stride=stride,
            cardinality=cardinality,
            bottleneck_width=bottleneck_width)
        if self.use_se:
            self.se = SEBlock(channels=out_channels)
        if self.resize_identity:
            if identity_conv3x3:
                self.identity_conv = resnext_conv3x3(
                    in_channels=in_channels,
                    out_channels=out_channels,
                    stride=stride,
                    groups=1,
                    activate=False)
            else:
                self.identity_conv = resnext_conv1x1(
                    in_channels=in_channels,
                    out_channels=out_channels,
                    stride=stride,
                    activate=False)
        self.activ = nn.ReLU(inplace=True)

    def forward(self, x):
        if self.resize_identity:
            identity = self.identity_conv(x)
        else:
            identity = x
        x = self.body(x)
        if self.use_se:
            x = self.se(x)
        x = x + identity
        x = self.activ(x)
        return x


class SEInitBlock(nn.Module):
    """
    SENet specific initial block.

    Parameters:
    ----------
    in_channels : int
        Number of input channels.
    out_channels : int
        Number of output channels.
    """
    def __init__(self,
                 in_channels,
                 out_channels):
        super(SEInitBlock, self).__init__()
        mid_channels = out_channels // 2

        self.conv1 = resnext_conv3x3(
            in_channels=in_channels,
            out_channels=mid_channels,
            stride=2,
            groups=1,
            activate=True)
        self.conv2 = resnext_conv3x3(
            in_channels=mid_channels,
            out_channels=mid_channels,
            stride=1,
            groups=1,
            activate=True)
        self.conv3 = resnext_conv3x3(
            in_channels=mid_channels,
            out_channels=out_channels,
            stride=1,
            groups=1,
            activate=True)
        self.pool = nn.MaxPool2d(
            kernel_size=3,
            stride=2,
            padding=1)

    def forward(self, x):
        x = self.conv1(x)
        x = self.conv2(x)
        x = self.conv3(x)
        x = self.pool(x)
        return x


class SENet(nn.Module):
    """
    SENet model from 'Squeeze-and-Excitation Networks,' https://arxiv.org/abs/1709.01507.

    Parameters:
    ----------
    channels : list of list of int
        Number of output channels for each unit.
    init_block_channels : int
        Number of output channels for the initial unit.
    cardinality: int
        Number of groups.
    bottleneck_width: int
        Width of bottleneck block.
    in_channels : int, default 3
        Number of input channels.
    num_classes : int, default 1000
        Number of classification classes.
    """
    def __init__(self,
                 channels,
                 init_block_channels,
                 cardinality,
                 bottleneck_width,
                 in_channels=3,
                 num_classes=1000):
        super(SENet, self).__init__()

        self.features = nn.Sequential()
        self.features.add_module("init_block", SEInitBlock(
            in_channels=in_channels,
            out_channels=init_block_channels))
        in_channels = init_block_channels
        for i, channels_per_stage in enumerate(channels):
            stage = nn.Sequential()
            identity_conv3x3 = (i != 0)
            for j, out_channels in enumerate(channels_per_stage):
                stride = 2 if (j == 0) and (i != 0) else 1
                stage.add_module("unit{}".format(j + 1), SENetUnit(
                    in_channels=in_channels,
                    out_channels=out_channels,
                    stride=stride,
                    cardinality=cardinality,
                    bottleneck_width=bottleneck_width,
                    identity_conv3x3=identity_conv3x3))
                in_channels = out_channels
            self.features.add_module("stage{}".format(i + 1), stage)
        self.features.add_module('final_pool', nn.AvgPool2d(
            kernel_size=7,
            stride=1))

        self.output = nn.Sequential()
        self.output.add_module('dropout', nn.Dropout(p=0.2))
        self.output.add_module('fc', nn.Linear(
            in_features=in_channels,
            out_features=num_classes))

        self._init_params()

    def _init_params(self):
        for name, module in self.named_modules():
            if isinstance(module, nn.Conv2d):
                init.kaiming_uniform_(module.weight)
                if module.bias is not None:
                    init.constant_(module.bias, 0)

    def forward(self, x):
        x = self.features(x)
        x = x.view(x.size(0), -1)
        x = self.output(x)
        return x


def get_senet(blocks,
              model_name=None,
              pretrained=False,
              root=os.path.join('~', '.torch', 'models'),
              **kwargs):
    """
    Create SENet model with specific parameters.

    Parameters:
    ----------
    blocks : int
        Number of blocks.
    model_name : str or None, default None
        Model name for loading pretrained model.
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    root : str, default '~/.mxnet/models'
        Location for keeping the model parameters.
    """

    if blocks == 52:
        layers = [3, 4, 6, 3]
        cardinality = 32
    elif blocks == 103:
        layers = [3, 4, 23, 3]
        cardinality = 32
    elif blocks == 154:
        layers = [3, 8, 36, 3]
        cardinality = 64
    else:
        raise ValueError("Unsupported SENet with number of blocks: {}".format(blocks))

    bottleneck_width = 4
    init_block_channels = 128
    channels_per_layers = [256, 512, 1024, 2048]

    channels = [[ci] * li for (ci, li) in zip(channels_per_layers, layers)]

    net = SENet(
        channels=channels,
        init_block_channels=init_block_channels,
        cardinality=cardinality,
        bottleneck_width=bottleneck_width,
        **kwargs)

    if pretrained:
        if (model_name is None) or (not model_name):
            raise ValueError("Parameter `model_name` should be properly initialized for loading pretrained model.")
        from .model_store import download_model
        download_model(
            net=net,
            model_name=model_name,
            local_model_store_dir_path=root)

    return net


def senet52(**kwargs):
    """
    SENet-52 model from 'Squeeze-and-Excitation Networks,' https://arxiv.org/abs/1709.01507.

    Parameters:
    ----------
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    root : str, default '~/.torch/models'
        Location for keeping the model parameters.
    """
    return get_senet(blocks=52, model_name="senet52", **kwargs)


def senet103(**kwargs):
    """
    SENet-103 model from 'Squeeze-and-Excitation Networks,' https://arxiv.org/abs/1709.01507.

    Parameters:
    ----------
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    root : str, default '~/.torch/models'
        Location for keeping the model parameters.
    """
    return get_senet(blocks=103, model_name="senet103", **kwargs)


def senet154(**kwargs):
    """
    SENet-154 model from 'Squeeze-and-Excitation Networks,' https://arxiv.org/abs/1709.01507.

    Parameters:
    ----------
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    root : str, default '~/.torch/models'
        Location for keeping the model parameters.
    """
    return get_senet(blocks=154, model_name="senet154", **kwargs)


def _test():
    import numpy as np
    import torch
    from torch.autograd import Variable

    pretrained = True

    models = [
        # senet52,
        # senet103,
        senet154,
    ]

    for model in models:

        net = model(pretrained=pretrained)

        net.train()
        net_params = filter(lambda p: p.requires_grad, net.parameters())
        weight_count = 0
        for param in net_params:
            weight_count += np.prod(param.size())
        # print("m={}, {}".format(model.__name__, weight_count))
        assert (model != senet52 or weight_count == 44659416)  # 22623272
        assert (model != senet103 or weight_count == 60963096)  # 38908456
        assert (model != senet154 or weight_count == 115088984)  # 93018024

        x = Variable(torch.randn(1, 3, 224, 224))
        y = net(x)
        assert (tuple(y.size()) == (1, 1000))


if __name__ == "__main__":
    _test()
