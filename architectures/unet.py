import torch
import torch.nn as nn
from .base import conv_mod


class GenNoise(nn.Module):
    def __init__(self, dim2):
        super(GenNoise, self).__init__()
        self.dim2 = dim2

    def forward(self, input):
        a = list(input.size())
        a[1] = self.dim2
        # print (input.data.type())

        b = torch.zeros(a).type_as(input.data)
        b.normal_()

        x = torch.autograd.Variable(b)

        return x


class Swish(nn.Module):
    """
        https://arxiv.org/abs/1710.05941
        The hype was so huge that I could not help but try it
    """

    def __init__(self):
        super(Swish, self).__init__()
        self.s = nn.Sigmoid()

    def forward(self, x):
        return x * self.s(x)


class ListModule(nn.Module):
    def __init__(self, *args):
        super(ListModule, self).__init__()
        idx = 0
        for module in args:
            self.add_module(str(idx), module)
            idx += 1

    def __getitem__(self, idx):
        if idx >= len(self._modules):
            raise IndexError('index {} is out of range'.format(idx))
        if idx < 0:
            idx = len(self) + idx

        it = iter(self._modules.values())
        for i in range(idx):
            next(it)
        return next(it)

    def __iter__(self):
        return iter(self._modules.values())

    def __len__(self):
        return len(self._modules)


class unetConv2(nn.Module):
    def __init__(self, in_size, out_size, norm_layer, need_bias, pad, act_fun):
        super(unetConv2, self).__init__()

        # print(pad)
        if norm_layer is not None:
            self.conv1 = nn.Sequential(conv_mod(in_size, out_size, 3, bias=need_bias, pad=pad),
                                       norm_layer(out_size), act_fun, )
            self.conv2 = nn.Sequential(conv_mod(out_size, out_size, 3, bias=need_bias, pad=pad),
                                       norm_layer(out_size), act_fun, )
        else:
            self.conv1 = nn.Sequential(conv_mod(in_size, out_size, 3, bias=need_bias, pad=pad), act_fun, )
            self.conv2 = nn.Sequential(conv_mod(out_size, out_size, 3, bias=need_bias, pad=pad), act_fun, )

    def forward(self, inputs):
        outputs = self.conv1(inputs)
        outputs = self.conv2(outputs)
        return outputs


class unetDown(nn.Module):
    def __init__(self, in_size, out_size, norm_layer, need_bias, pad, act_fun):
        super(unetDown, self).__init__()
        self.conv = unetConv2(in_size, out_size, norm_layer, need_bias, pad, act_fun)
        self.down = nn.MaxPool2d(2, 2)

    def forward(self, inputs):
        outputs = self.down(inputs)
        outputs = self.conv(outputs)
        return outputs


class unetUp(nn.Module):
    def __init__(self, out_size, upsample_mode, need_bias, pad, act_fun, same_num_filt=False):
        super(unetUp, self).__init__()

        num_filt = out_size if same_num_filt else out_size * 2
        if upsample_mode == 'deconv':
            self.up = nn.ConvTranspose2d(num_filt, out_size, 4, stride=2, padding=1)
            self.conv = unetConv2(out_size * 2, out_size, None, need_bias, pad, act_fun)
        elif upsample_mode == 'bilinear' or upsample_mode == 'nearest':
            self.up = nn.Sequential(nn.Upsample(scale_factor=2, mode=upsample_mode),
                                    conv_mod(num_filt, out_size, 3, bias=need_bias, pad=pad))
            self.conv = unetConv2(out_size * 2, out_size, None, need_bias, pad, act_fun)
        else:
            assert False

    def forward(self, inputs1, inputs2):
        in1_up = self.up(inputs1)

        if (inputs2.size(2) != in1_up.size(2)) or (inputs2.size(3) != in1_up.size(3)):
            diff2 = (inputs2.size(2) - in1_up.size(2)) // 2
            diff3 = (inputs2.size(3) - in1_up.size(3)) // 2
            inputs2_ = inputs2[:, :, diff2: diff2 + in1_up.size(2), diff3: diff3 + in1_up.size(3)]
        else:
            inputs2_ = inputs2

        output = self.conv(torch.cat([in1_up, inputs2_], 1))

        return output
    
    
class UNet(nn.Module):
    """
        upsample_mode in ['deconv', 'nearest', 'bilinear']
        pad in ['zero', 'replication', 'none']
    """

    def __init__(self, num_input_channels=3, num_output_channels=3,
                 filters=[16, 32, 64, 128, 256], more_layers=0, concat_x=False,
                 activation='ReLU', upsample_mode='deconv', pad='zero',
                 norm_layer=nn.InstanceNorm2d, need_sigmoid=True, need_bias=True):
        super(UNet, self).__init__()

        self.more_layers = more_layers
        self.concat_x = concat_x

        if activation == "ReLU":
            act_fun = nn.ReLU()
        elif activation == "Tanh":
            act_fun = nn.Tanh()
        elif activation == "LeakyReLU":
            act_fun = nn.LeakyReLU(0.2, inplace=True)
        else:
            raise ValueError("Activation has to be in [ReLU, Tanh, LeakyReLU]")

        self.start = unetConv2(num_input_channels, filters[0] if not concat_x else filters[0] - num_input_channels,
                               norm_layer, need_bias, pad, act_fun)

        self.down1 = unetDown(filters[0], filters[1] if not concat_x else filters[1] - num_input_channels, norm_layer,
                              need_bias, pad, act_fun)
        self.down2 = unetDown(filters[1], filters[2] if not concat_x else filters[2] - num_input_channels, norm_layer,
                              need_bias, pad, act_fun)
        self.down3 = unetDown(filters[2], filters[3] if not concat_x else filters[3] - num_input_channels, norm_layer,
                              need_bias, pad, act_fun)
        self.down4 = unetDown(filters[3], filters[4] if not concat_x else filters[4] - num_input_channels, norm_layer,
                              need_bias, pad, act_fun)

        # more downsampling layers
        if self.more_layers > 0:
            self.more_downs = [
                unetDown(filters[4], filters[4] if not concat_x else filters[4] - num_input_channels, norm_layer,
                         need_bias, pad, act_fun) for i in range(self.more_layers)]
            self.more_ups = [unetUp(filters[4], upsample_mode, need_bias, pad, act_fun, same_num_filt=True) for i in
                             range(self.more_layers)]

            self.more_downs = ListModule(*self.more_downs)
            self.more_ups = ListModule(*self.more_ups)

        self.up4 = unetUp(filters[3], upsample_mode, need_bias, pad, act_fun)
        self.up3 = unetUp(filters[2], upsample_mode, need_bias, pad, act_fun)
        self.up2 = unetUp(filters[1], upsample_mode, need_bias, pad, act_fun)
        self.up1 = unetUp(filters[0], upsample_mode, need_bias, pad, act_fun)

        self.final = conv_mod(filters[0], num_output_channels, 1, bias=need_bias, pad=pad)

        if need_sigmoid:
            self.final = nn.Sequential(self.final, nn.Sigmoid())

    def forward(self, inputs):

        # Downsample
        downs = [inputs]
        down = nn.AvgPool2d(2, 2)
        for i in range(4 + self.more_layers):
            downs.append(down(downs[-1]))

        in64 = self.start(inputs)
        if self.concat_x:
            in64 = torch.cat([in64, downs[0]], 1)

        down1 = self.down1(in64)
        if self.concat_x:
            down1 = torch.cat([down1, downs[1]], 1)

        down2 = self.down2(down1)
        if self.concat_x:
            down2 = torch.cat([down2, downs[2]], 1)

        down3 = self.down3(down2)
        if self.concat_x:
            down3 = torch.cat([down3, downs[3]], 1)

        down4 = self.down4(down3)
        if self.concat_x:
            down4 = torch.cat([down4, downs[4]], 1)

        if self.more_layers > 0:
            prevs = [down4]
            for kk, d in enumerate(self.more_downs):
                # print(prevs[-1].size())
                out = d(prevs[-1])
                if self.concat_x:
                    out = torch.cat([out, downs[kk + 5]], 1)

                prevs.append(out)

            up_ = self.more_ups[-1](prevs[-1], prevs[-2])
            for idx in range(self.more_layers - 1):
                l = self.more_ups[self.more - idx - 2]
                up_ = l(up_, prevs[self.more - idx - 2])
        else:
            up_ = down4

        up4 = self.up4(up_, down3)
        up3 = self.up3(up4, down2)
        up2 = self.up2(up3, down1)
        up1 = self.up1(up2, in64)

        return self.final(up1)


__all__ = [
    "UNet",
]
