import numpy as np

# # M-MSA
# 生成基函数矩阵
def generate_data_for_mmsa(cpx_in, cpx_out, mem_len): # cpx_in, cpx_out 模型输入与输出
    len_data = len(cpx_in)
    cpx_in_reshape = np.reshape(cpx_in, [-1, 1])
    in_abs = np.abs(cpx_in_reshape) # 信号幅值
    cpx_in_xn = cpx_in_reshape[mem_len:, :]
    # in_phase = np.divide(cpx_in_reshape, in_abs) # 求取信号相位
    X = np.zeros((len_data - mem_len, 0), 'complex')
    # 格式：Ai,j, Bi,j。i代表记忆深度,j代表项(term)
    #aki*x(n-i)
    # X = np.hstack((X, cpx_in_reshape[mem_len:, :]))
    # 对应A0,23   唯一一个记忆项i可以取到0，单独拿出来
    X = np.hstack((X, np.multiply(in_abs[mem_len:,:], cpx_in_reshape[mem_len:, :]))) #X矩阵中拼接 x(n)*|x(n)|
    X = np.hstack((X, cpx_in_reshape[mem_len:,:]))
    # 对应A0,21与B0,21
    tmp = np.zeros((len_data - mem_len, 0), 'complex')
    tmp = np.hstack((tmp, cpx_in_reshape[mem_len :,:]))  # 21项 构建x(n-i)
    tmp1 = np.multiply(tmp, in_abs[mem_len:, :])  # 构建绝对值的记忆项x(n-i)*|x(n)|
    tmp2 = np.multiply(tmp1, in_abs[mem_len :,:])  # 构建x(n-i)*|x(n)|*|x(n-i)|
    X = np.hstack((X, tmp2))
    X = np.hstack((X, tmp1))
    for i in range(1, mem_len+1): # 根据效果可选择注释部分项
        tmp = np.zeros((len_data - mem_len, 0), 'complex')
        # 对应Ai,21与Bi,21
        tmp  = np.hstack((tmp, cpx_in_reshape[mem_len - i:-i]))     # 21项 构建x(n-i)
        tmp1 = np.multiply(tmp, in_abs[mem_len:,:])              #构建绝对值的记忆项x(n-i)*|x(n)|
        tmp2 = np.multiply(tmp1,in_abs[mem_len-i:-i])                #构建x(n-i)*|x(n)|*|x(n-i)|
        X = np.hstack((X, tmp2))
        X = np.hstack((X, tmp1))
        #对应Ai,22与Bi,22
        X = np.hstack((X, np.multiply(in_abs[mem_len - i: -i], cpx_in_reshape[mem_len:, :])))
        X = np.hstack((X, cpx_in_reshape[mem_len:, :]))
        #对应Ai,23与Bi,23
        X = np.hstack((X, np.multiply(in_abs[mem_len - i: -i], cpx_in_reshape[mem_len - i: -i])))
        X = np.hstack((X, cpx_in_reshape[mem_len - i: -i]))
        # 对应Ai,24与Bi,24
        X = np.hstack((X, np.multiply(in_abs[mem_len:, :], cpx_in_reshape[mem_len - i: -i])))
        X = np.hstack((X, cpx_in_reshape[mem_len - i: -i]))
        # #对应Ai,25与Bi,25
        # tmp = np.multiply(np.square(cpx_in_reshape[mem_len:, :]), np.conj(cpx_in_reshape[mem_len - i: -i]))
        # X = np.hstack((X, np.multiply(in_abs[mem_len:, :], tmp)))
        # X = np.hstack((X, tmp))
    return X, cpx_out[mem_len:], cpx_in_xn

# 分段提取系数，降低系数提取复杂度。（查看文献）
def sort_and_compute_para_for_mmsa(cpx_in, input_sample, cpx_out, k, beta):
    ###分段提取参数###
    C_group = np.zeros((0, input_sample.shape[1]))

    in_abs = np.abs(cpx_in[:, 0])
    for j in range(k):
        #将不同的段的幅值的索引值
        idx = np.where((in_abs > beta[j]) & (in_abs <= beta[j+1]))
        # 分段使用spsa提取系数
        # C_group = np.vstack((C_group, spsa_compute_para(input_sample[idx], cpx_out[idx], 1000)))
        # 分段使用最小二乘法提取系数
        C_group = np.vstack((C_group, np.dot(np.linalg.pinv(input_sample[idx]), cpx_out[idx])))
        # 1-bit脊回归
        # delta = 1/np.power(2, 16)
        # matrix = np.dot(input_sample[idx].conj().T, input_sample[idx]) + delta*np.identity(input_sample.shape[1])
        # C_group = np.vstack((C_group, np.dot(np.dot(np.linalg.inv(matrix),input_sample[idx].conj().T) , cpx_out[idx])))
    #################
    ###整体提取参数###
    #################

    C_group_quant = C_group*np.power(2, 10)
    #print(C_group)
    C_group_quant_max_real = np.max(np.abs(np.real(C_group_quant)))
    C_group_quant_max_imag = np.max(np.abs(np.imag(C_group_quant)))
    #print("C_group_quant_max_real = *****************\n",C_group_quant_max_real)
    #print("C_group_quant_max_imag = *****************\n",C_group_quant_max_imag)

    # for i in range(input_sample.shape[1]):
    #     mem_data = C_group_quant[:,i]
    #     filename1 = "E:/0_DPD/M-MSA/lut_M_MSA/{}_a.coe".format(i)
    #     filename2 = "E:/0_DPD/M-MSA/lut_M_MSA/{}_b.coe".format(i)
    #     with open(filename1, 'w') as f:
    #         # f.write("MEMORY_INITIALIZATION_RADIX=10;\nMEMORY_INITIALIZATION_VECTOR=\n")
    #         for data in mem_data:
    #             f.write("{}\n".format(int(np.real(data))))
    #     with open(filename2, 'w') as f:
    #         # f.write("MEMORY_INITIALIZATION_RADIX=10;\nMEMORY_INITIALIZATION_VECTOR=\n")
    #         for data in mem_data:
    #             f.write("{}\n".format(int(np.imag(data))))

    # filename1 = "C:/Users/master/Desktop/dpd/ddr/k.txt"
    # for i in range(5):
    #     mem_data = C_group_quant[i, :]
    #     # filename1 = "./lut_msa_k/{}_k.coe".format(i)
    #
    #     with open(filename1, 'a+') as f:
    #         for data in mem_data:
    #             f.write("{}\n".format(int(np.real(data))))
    #             f.write("{}\n".format(int(np.imag(data))))

    #print("C_group***********************\n", C_group.shape)
    return C_group

# 生成预失真信号    之前I-MSA是将基函数输入，是因为基函数第一列就是x(0)-x(n),正好是输入数据，结构也正确。
#修改之后，基函数结构没有原先输入的结构的数据，所以改变这种输入数据的方式，重新输入所需对应数据。
def compute_u_for_mmsa(cpx_in: object, input_sample: object, k: object, para: object, beta: object) -> object: # para为提取得到的系数
    u = np.zeros((input_sample.shape[0],), 'complex')
    in_abs = np.abs(cpx_in[:, 0])
    for j in range(k): # 与sort_and_compute_para对应，也需要分段后与各自分段的系数相乘
        idx = np.where((in_abs > beta[j]) & (in_abs <= beta[j+1]))
        u[idx] = np.dot(input_sample[idx], para[j])
    return u



# # I-MSA
# 生成基函数矩阵
def generate_data_for_imsa(cpx_in, cpx_out, mem_len): # cpx_in, cpx_out 模型输入与输出
    len_data = len(cpx_in)
    cpx_in_reshape = np.reshape(cpx_in, [-1, 1])
    in_abs = np.abs(cpx_in_reshape) # 信号幅值
    in_phase = np.divide(cpx_in_reshape, in_abs) # 求取信号相位
    X = np.zeros((len_data - mem_len, 0), 'complex')
    # 格式：Ai,j, Bi,j。i代表记忆深度,j代表项(term)
    # 对应A0,1与B0,1
    X = np.hstack((X, cpx_in_reshape[mem_len:, :]))
    X = np.hstack((X, in_phase[mem_len:, :]))
    # 对应A0,21与B0,21
    X = np.hstack((X, np.multiply(X, in_abs[mem_len:, :])))
    for i in range(1, mem_len+1): # 根据效果可选择注释部分项
        tmp = np.zeros((len_data - mem_len, 0), 'complex')
        # 对应Ai,1与Bi,1
        tmp = np.hstack((tmp, cpx_in_reshape[mem_len-i:-i]))
        tmp = np.hstack((tmp, in_phase[mem_len-i:-i]))
        X = np.hstack((X, tmp))
        # 对应Ai,21与Bi,21，复用tmp
        X = np.hstack((X, np.multiply(tmp, in_abs[mem_len:, :])))
        #对应Ai,22与Bi,22
        X = np.hstack((X, np.multiply(in_abs[mem_len - i: -i], cpx_in_reshape[mem_len:, :])))
        X = np.hstack((X, cpx_in_reshape[mem_len:, :]))
        #对应Ai,23与Bi,23
        X = np.hstack((X, np.multiply(in_abs[mem_len - i: -i], cpx_in_reshape[mem_len - i: -i])))
        X = np.hstack((X, cpx_in_reshape[mem_len - i: -i]))
        #对应Ai,24与Bi,24
        # X = np.hstack((X, np.multiply(in_abs[mem_len:, :], cpx_in_reshape[mem_len - i: -i])))
        # X = np.hstack((X, cpx_in_reshape[mem_len - i: -i]))
        #对应Ai,25与Bi,25
        # tmp = np.multiply(np.square(cpx_in_reshape[mem_len:, :]), np.conj(cpx_in_reshape[mem_len - i: -i]))
        # X = np.hstack((X, np.multiply(in_abs[mem_len:, :], tmp)))
        # X = np.hstack((X, tmp))
    return X, cpx_out[mem_len:]

# 分段提取系数，降低系数提取复杂度。（查看文献）
def sort_and_compute_para_for_imsa(input_sample, cpx_out, k, beta):
    ###分段提取参数###
    C_group = np.zeros((0, input_sample.shape[1]))
    in_abs = np.abs(input_sample[:, 0])
    for j in range(k):
        idx = np.where((in_abs > beta[j]) & (in_abs <= beta[j+1]))
        # 分段使用spsa提取系数
        # C_group = np.vstack((C_group, spsa_compute_para(input_sample[idx], cpx_out[idx], 1000)))
        # 分段使用最小二乘法提取系数
        C_group = np.vstack((C_group, np.dot(np.linalg.pinv(input_sample[idx]), cpx_out[idx])))
        # 1-bit脊回归
        # delta = 1/np.power(2, 16)
        # matrix = np.dot(input_sample[idx].conj().T, input_sample[idx]) + delta*np.identity(input_sample.shape[1])
        # C_group = np.vstack((C_group, np.dot(np.dot(np.linalg.inv(matrix),input_sample[idx].conj().T) , cpx_out[idx])))
    #################


    # C_group_quant = C_group*np.power(2, 10)
    # C_group_quant_max_real = np.max(np.abs(np.real(C_group_quant)))
    # C_group_quant_max_imag = np.max(np.abs(np.imag(C_group_quant)))
    #
    #
    # filename1 = "C:/Users/master/Desktop/dpd/ddr/k.txt"
    # for i in range(k):
    #     mem_data = C_group_quant[i, :]
    #     # filename1 = "./lut_msa_k/{}_k.coe".format(i)
    #
    #     with open(filename1, 'a+') as f:
    #         for data in mem_data:
    #             f.write("{}\n".format(int(np.real(data))))
    #             f.write("{}\n".format(int(np.imag(data))))

    return C_group

# 生成预失真信号
def compute_u_for_imsa(input_sample, k, para, beta): # para为提取得到的系数
    u = np.zeros((input_sample.shape[0],), 'complex')
    in_abs = np.abs(input_sample[:, 0])

    for j in range(k): # 与sort_and_compute_para对应，也需要分段后与各自分段的系数相乘
        idx = np.where((in_abs > beta[j]) & (in_abs <= beta[j+1]))
        u[idx] = np.dot(input_sample[idx], para[j])
        if j == k-1:
            idx_all = idx
    return u, idx_all



# # MP
# 生成基函数矩阵
def generate_data_for_mp(PA_input_complex, PA_output_complex, mem_deep, polyOrder):
    # 输入信号经过模型参数后计算结果
    len_data = len(PA_input_complex)
    if len_data <= mem_deep:
        return np.array([None])
    X_base = np.zeros((len_data, polyOrder), dtype='complex')
    x_abs = np.abs(PA_input_complex)
    for i in range(polyOrder):
        X_base[:, i] = PA_input_complex * (x_abs ** i)
    X = X_base[mem_deep:, :]
    for i in range(1, mem_deep):
        X = np.c_[X, X_base[mem_deep - i: -i, :]]
    # y_fit = np.dot(X, w)
    return X, PA_output_complex[mem_deep:]

# 生成预失真信号
def compute_u_for_mp(X, y):
    para = np.dot(np.linalg.pinv(X), y)
    y_fit = np.dot(X, para)
    return para, y_fit


# # GMP
# GMP(Generalized-memory-polynomial) 模型 参数提取 输入 x(n)、y(n),记忆深度men_len,阶数order_len,提取正向反向模型参数para，para_1
def generate_data_for_gmp(input_complex,output_complex,M,K,L):
    len_data = len(input_complex)
    y = output_complex[M+L:-L]
    X =  np.zeros((len_data-M-2*L,),"complex")

    for j in range(M+1):
        for i in range(K):
            X = np.c_[X,input_complex[M+L-j:len_data-j-L]*(np.abs(input_complex[M+L-j:len_data-j-L])**i)]

    for j in range(M+1):
        for i in range(1,K+1):
            for l in range(1,L):
                X = np.c_[X,input_complex[M+L-j:len_data-j-L]*(np.abs(input_complex[M+L-j-l:len_data-j-L-l])**i)]
    for j in range(M+1):
        for i in range(1,K+1):
            for l in range(1,L):
                X = np.c_[X,input_complex[M+L-j:len_data-j-L]*(np.abs(input_complex[M+L-j+l:len_data-j-L+l])**i)]

    # LS para para_dpd
    X = X[:,1:]
    return X,y

def compute_u_for_gmp(X,y):
    para = np.dot(np.linalg.pinv(X),y)
    y_fit = np.dot(X,para)
    return para,y_fit



# # DDR

# cpx_in, cpx_out 模型输入输出 mem_len 记忆深度 P 多项式阶数
def generate_data_for_ddr(cpx_in, cpx_out, mem_len, P):
    data_len = len(cpx_in)
    cpx_in_reshape = np.reshape(cpx_in, [-1, 1])
    in_square = np.square(np.abs(cpx_in_reshape))
    term1_base = np.zeros((data_len-mem_len, 0), dtype='double')
    base = np.zeros((data_len-mem_len, 0), dtype='double')
    for i in range(int((P-1)/2)+1):
        term1_base = np.hstack((term1_base, in_square[mem_len:] ** i))
        if i > 0:
            base = np.hstack((base, in_square[mem_len:] ** (i-1)))
    tmp = cpx_in_reshape[mem_len:]
    term2_tmp = np.multiply(base, np.square(tmp))
    term3_tmp = np.multiply(base, tmp)
    term4_tmp = np.multiply(base, np.conj(tmp))
    X = np.multiply(term1_base, tmp) #第一项中的记忆项 = 0部分
    for i in range(1, mem_len+1):
        #第一项的1<=记忆项<=mem_len
        X = np.hstack((X, np.multiply(term1_base, cpx_in_reshape[mem_len - i: -i, :])))
        #第二项的1<=记忆项<=mem_len
        X = np.hstack((X, np.multiply(term2_tmp, np.conj(cpx_in_reshape[mem_len - i: -i, :]))))
        #第三项的1<=记忆项<=mem_len
        X = np.hstack((X, np.multiply(term3_tmp, in_square[mem_len - i: -i, :])))
        #第四项的1<=记忆项<=mem_len
        X = np.hstack((X, np.multiply(term4_tmp, np.square(cpx_in_reshape[mem_len - i: -i, :]))))
    return X, cpx_out[mem_len:]

# 系数提取函数
# cpx_in, cpx_out 模型输入输出 mem_len 记忆深度 polyOrder 多项式阶数
def compute_u_for_ddr(X, y):
    w = np.dot(np.linalg.pinv(X), y)
    y_fit = np.dot(X, w)
    # 返回拟合值和参数
    # print("train_NMSE:{:.3f}dB".format(NMSE(y, y_fit)))
    return w, y_fit



# # DVR
def generate_data_for_dvr(cpx_in, cpx_out, mem_len, beta): # cpx_in, cpx_out为模型输入输出
    data_len = len(cpx_in)
    data_in = np.reshape(cpx_in, [-1, 1]) # 转化成一列
    data_abs = np.abs(data_in) # 求幅值
    data_phase = np.divide(data_in, data_abs) # 求复相角
    X = np.zeros((data_len - mem_len, 0), 'complex')
    ### M = 0 ###
    # linear
    X = np.hstack((X, data_in[mem_len:])) # 将参数水平方向堆叠
    outer_abs_mem0 = np.abs(np.subtract(data_abs[mem_len:], beta))
    # 1-st-order basis
    first_basis = np.multiply(outer_abs_mem0, data_phase[mem_len:])
    X = np.hstack((X, first_basis))
    # 2nd-order type-1
    X = np.hstack((X, np.multiply(first_basis, data_abs[mem_len:])))
    #############
    ### M = (1, mem_len) ###
    for i in range(1, mem_len+1): # 根据效果可注释掉一些项以减小复杂度
        # linear
        X = np.hstack((X, data_in[mem_len-i:data_len-i]))
        outer_abs_memi = np.abs(np.subtract(data_abs[mem_len-i:data_len-i], beta))
        # 1-st-order basis
        first_basis = np.multiply(outer_abs_memi, data_phase[mem_len-i:data_len-i])
        X = np.hstack((X, first_basis))
        # 2nd-order type-1
        # X = np.hstack((X, np.multiply(first_basis, data_abs[mem_len:])))
        # # 2nd-order type-2
        # X = np.hstack((X, np.multiply(outer_abs_memi, data_in[mem_len:])))
        # 2nd-order type-3
        # X = np.hstack((X, np.multiply(outer_abs_memi, data_in[mem_len-i:data_len-i])))
        # # DDR term-1
        # X = np.hstack((X, np.multiply(outer_abs_mem0, data_in[mem_len-i:data_len-i])))
        # # DDR term-2
        # tmp = np.multiply(np.square(data_in[mem_len:]), np.conj(data_in[mem_len-i:data_len-i]))
        # X = np.hstack((X, np.multiply(outer_abs_mem0, tmp)))
    Y = cpx_out[mem_len:]
    return X, Y

# 生成预失真信号
def compute_u_for_dvr(cpx_in, mem_len, beta, C): # cpx_in, cpx_out为模型输入输出
    # o_len = len(cpx_in)
    # cpx_in = np.append(cpx_in[o_len-mem_len:], cpx_in) # 使生成的预失真信号仍然为循环信号
    data_len = len(cpx_in)
    data_in = np.reshape(cpx_in, [-1, 1])
    data_abs = np.abs(data_in)
    data_phase = np.divide(data_in, data_abs)
    X = np.zeros((data_len - mem_len, 0), 'complex')
    ### M = 0 ###
    # linear
    X = np.hstack((X, data_in[mem_len:]))
    outer_abs_mem0 = np.abs(np.subtract(data_abs[mem_len:], beta))
    # 1-st-order basis
    first_basis = np.multiply(outer_abs_mem0, data_phase[mem_len:])
    X = np.hstack((X, first_basis))
    # 2nd-order type-1
    X = np.hstack((X, np.multiply(first_basis, data_abs[mem_len:])))
    #############
    ### M = (1, mem_len) ###
    for i in range(1, mem_len+1): # 根据效果可注释掉一些项以减小复杂度，与基函数矩阵生成函数匹配
        # linear
        X = np.hstack((X, data_in[mem_len-i:data_len-i]))
        outer_abs_memi = np.abs(np.subtract(data_abs[mem_len-i:data_len-i], beta))
        # 1-st-order basis
        first_basis = np.multiply(outer_abs_memi, data_phase[mem_len-i:data_len-i])
        X = np.hstack((X, first_basis))
        # 2nd-order type-1
        # X = np.hstack((X, np.multiply(first_basis, data_abs[mem_len:])))
        # # 2nd-order type-2
        # X = np.hstack((X, np.multiply(outer_abs_memi, data_in[mem_len:])))
        # # 2nd-order type-3
        # X = np.hstack((X, np.multiply(outer_abs_memi, data_in[mem_len-i:data_len-i])))
        # # DDR term-1
        # X = np.hstack((X, np.multiply(outer_abs_mem0, data_in[mem_len-i:data_len-i])))
        # # DDR term-2
        # tmp = np.multiply(np.square(data_in[mem_len:]), np.conj(data_in[mem_len-i:data_len-i]))
        # X = np.hstack((X, np.multiply(outer_abs_mem0, tmp)))
    #####计算U#####
    U = np.dot(X, C)
    return U
