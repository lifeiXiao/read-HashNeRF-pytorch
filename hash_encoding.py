import torch
import torch.nn as nn

from utils import get_voxel_vertices


class HashEmbedder(nn.Module): #继承自nn.Module，pytorch深度学习框架的基类
    def __init__(self, bounding_box, n_levels=16, n_features_per_level=2,
                 log2_hashmap_size=19, base_resolution=16, finest_resolution=512):
        """
        bounding_box: 跟特定场景有关的参数
        n_levels: 论文中的L,层级数
        n_features_per_level: 论文中的F，每层的特征数
        log2_hashmap_size: 论文中的T参数的指数值 ,超参数
        base_resolution: 就是Nmin, 16
        finest_resolution: Nmax, 超参数
        """
        super(HashEmbedder, self).__init__()
        # 场景的bbox

        #设置了传入的参数，并计算了输出维度 
        self.bounding_box = bounding_box

        self.n_levels = n_levels

        self.n_features_per_level = n_features_per_level

        self.log2_hashmap_size = log2_hashmap_size

        self.base_resolution = torch.tensor(base_resolution)
        
        self.finest_resolution = torch.tensor(finest_resolution)
        # 一共16层，每层的特征长度是F=2, 因此这里是32 #输出维度（由层级数和每层特征数决定）
        self.out_dim = self.n_levels * self.n_features_per_level

        # 论文中的公式3
        #计算每个层级的分辨率
        # 根据基本分别率和最细分辨率确定每个层级的分辨率
                     
        self.b = torch.exp((torch.log(self.finest_resolution) - torch.log(self.base_resolution)) / (n_levels - 1))

        #创建了一个嵌入层，每个层级都有一个嵌入层，嵌入曾的大小由哈希表的大小决定，每个嵌入有特定数量的特征。
        # 16个Embedding,大小是T，这里的维度是 [2**19,2]
        self.embeddings = nn.ModuleList([nn.Embedding(2 ** self.log2_hashmap_size,
                                                      self.n_features_per_level) for i in range(n_levels)])

        # custom uniform initialization
        # embeddings 参数的初始化
        #对每个嵌入层，都进行初始化，要保证初始值接近0但不完全相同
        for i in range(n_levels):
            nn.init.uniform_(self.embeddings[i].weight, a=-0.0001, b=0.0001)
            # self.embeddings[i].weight.data.zero_()

    #trilinear_interp是一个执行三线性插值的方法，用于在3D空间平滑的插值，它接受一个点X和它所在的体素的相关信息，然后基于这些信息计算该点的嵌入值
    def trilinear_interp(self, x, voxel_min_vertex, voxel_max_vertex, voxel_embedds):
        """
        立方体8个点的三线性插值的计算
        x: B x 3
        voxel_min_vertex: B x 3
        voxel_max_vertex: B x 3
        voxel_embedds: B x 8 x 2
        """
        # source: https://en.wikipedia.org/wiki/Trilinear_interpolation
        weights = (x - voxel_min_vertex) / (voxel_max_vertex - voxel_min_vertex)  # B x 3
        # 第一次插值 8变4
        # step 1
        # 0->000, 1->001, 2->010, 3->011, 4->100, 5->101, 6->110, 7->111
        c00 = voxel_embedds[:, 0] * (1 - weights[:, 0][:, None]) + voxel_embedds[:, 4] * weights[:, 0][:, None]
        c01 = voxel_embedds[:, 1] * (1 - weights[:, 0][:, None]) + voxel_embedds[:, 5] * weights[:, 0][:, None]
        c10 = voxel_embedds[:, 2] * (1 - weights[:, 0][:, None]) + voxel_embedds[:, 6] * weights[:, 0][:, None]
        c11 = voxel_embedds[:, 3] * (1 - weights[:, 0][:, None]) + voxel_embedds[:, 7] * weights[:, 0][:, None]
        # 第二次插值 4变2
        # step 2
        c0 = c00 * (1 - weights[:, 1][:, None]) + c10 * weights[:, 1][:, None]
        c1 = c01 * (1 - weights[:, 1][:, None]) + c11 * weights[:, 1][:, None]
        # 第三次插值 2变1 得出最终的值
        # step 3
        c = c0 * (1 - weights[:, 2][:, None]) + c1 * weights[:, 2][:, None]

        return c

    # forward方法是模型的核心，它定义了输入数据是如何通过网络传递，对于每个输入点，它在每个层级上，执行哈希查找
    #和三线性插值，然后将这些结果连接起来形成最终的特征向量
    def forward(self, x):
        # x is 3D point position: B x 3
        x_embedded_all = []
        # n_levels is 16 16个层级
        for i in range(self.n_levels):
            # base_resolution 16
            # b 1.2599 论文中的公式3, resolution 就是论文中的公式2的Nl
            resolution = torch.floor(self.base_resolution * self.b ** i)

            # hashed_voxel_indices [bs, 8]
            voxel_min_vertex, voxel_max_vertex, hashed_voxel_indices = get_voxel_vertices(
                x, self.bounding_box,
                resolution, self.log2_hashmap_size)
            # [bs,8,2]
            # 取出在hash table中的索引值对应的value
            voxel_embedds = self.embeddings[i](hashed_voxel_indices)

            # [bs,2]
            x_embedded = self.trilinear_interp(x, voxel_min_vertex, voxel_max_vertex, voxel_embedds)

            x_embedded_all.append(x_embedded)
        # 16层的cat在一起 [bs,16*2]
        return torch.cat(x_embedded_all, dim=-1)


# ----------------------------------------------------------------------------------------------------------------------

# 视角方向的编码
#SHEcoder类用于球谐编码，这是一种在图形学中常用的技术，它初始化了一些参数和系数，这些系数用于计算输入方向的球谐特征
class SHEncoder(nn.Module):
    def __init__(self, input_dim=3, degree=4):
        """
        这两个参数都是默认值
        """

        super().__init__()

        self.input_dim = input_dim
        self.degree = degree

        assert self.input_dim == 3
        assert self.degree >= 1 and self.degree <= 5

        self.out_dim = degree ** 2

        self.C0 = 0.28209479177387814
        self.C1 = 0.4886025119029199
        self.C2 = [
            1.0925484305920792,
            -1.0925484305920792,
            0.31539156525252005,
            -1.0925484305920792,
            0.5462742152960396
        ]
        self.C3 = [
            -0.5900435899266435,
            2.890611442640554,
            -0.4570457994644658,
            0.3731763325901154,
            -0.4570457994644658,
            1.445305721320277,
            -0.5900435899266435
        ]
        self.C4 = [
            2.5033429417967046,
            -1.7701307697799304,
            0.9461746957575601,
            -0.6690465435572892,
            0.10578554691520431,
            -0.6690465435572892,
            0.47308734787878004,
            -1.7701307697799304,
            0.6258357354491761
        ]

    #forward方法计算输入向量的球谐特征，这个过程涉及到使用球谐系数和输入向量的x,y,z分量来计算特征值
    def forward(self, input, **kwargs):

        result = torch.empty((*input.shape[:-1], self.out_dim), dtype=input.dtype, device=input.device)
        x, y, z = input.unbind(-1)

        result[..., 0] = self.C0
        if self.degree > 1:
            result[..., 1] = -self.C1 * y
            result[..., 2] = self.C1 * z
            result[..., 3] = -self.C1 * x
            if self.degree > 2:
                xx, yy, zz = x * x, y * y, z * z
                xy, yz, xz = x * y, y * z, x * z
                result[..., 4] = self.C2[0] * xy
                result[..., 5] = self.C2[1] * yz
                result[..., 6] = self.C2[2] * (2.0 * zz - xx - yy)
                # result[..., 6] = self.C2[2] * (3.0 * zz - 1) # xx + yy + zz == 1, but this will lead to different backward gradients, interesting...
                result[..., 7] = self.C2[3] * xz
                result[..., 8] = self.C2[4] * (xx - yy)
                if self.degree > 3:
                    result[..., 9] = self.C3[0] * y * (3 * xx - yy)
                    result[..., 10] = self.C3[1] * xy * z
                    result[..., 11] = self.C3[2] * y * (4 * zz - xx - yy)
                    result[..., 12] = self.C3[3] * z * (2 * zz - 3 * xx - 3 * yy)
                    result[..., 13] = self.C3[4] * x * (4 * zz - xx - yy)
                    result[..., 14] = self.C3[5] * z * (xx - yy)
                    result[..., 15] = self.C3[6] * x * (xx - 3 * yy)
                    if self.degree > 4:
                        result[..., 16] = self.C4[0] * xy * (xx - yy)
                        result[..., 17] = self.C4[1] * yz * (3 * xx - yy)
                        result[..., 18] = self.C4[2] * xy * (7 * zz - 1)
                        result[..., 19] = self.C4[3] * yz * (7 * zz - 3)
                        result[..., 20] = self.C4[4] * (zz * (35 * zz - 30) + 3)
                        result[..., 21] = self.C4[5] * xz * (7 * zz - 3)
                        result[..., 22] = self.C4[6] * (xx - yy) * (7 * zz - 1)
                        result[..., 23] = self.C4[7] * xz * (xx - 3 * yy)
                        result[..., 24] = self.C4[8] * (xx * (xx - 3 * yy) - yy * (3 * xx - yy))

        return result
