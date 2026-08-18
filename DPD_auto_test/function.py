import numpy as np
import matplotlib.pyplot as plt
import tensorflow as tf
import tarfile
import shutil
from scipy import signal
from scipy.signal import kaiser
# import nptdms as tdms
from nptdms import TdmsFile

# import nptdms as tdms
# from nptdms import TdmsFile

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei']
plt.rcParams['axes.unicode_minus'] = False  # 用来正常显示负号


# 载入数据并获取IQ信号、保存路径、复信号
def load_data(path, data_name):
    # 读取文件名
    if data_name.endswith('.txt'):
        data_input_path = path + data_name
        # 移除.txt扩展名用于生成保存文件名
        base_name = data_name[:-4]
        data_save_path = path + base_name + '_Adj.txt'
    else:
        data_input_path = path + data_name + '.txt'
        # 保存文件名
        data_save_path = path + data_name + '_Adj.txt'
    
    data = np.loadtxt(data_input_path)
    # 生成复数基带信号
    data_cp = data[:, 0] + 1j * data[:, 1]
    return data, data_save_path, data_cp


# 读取tdms文件数据并将IQ交织写成复信号
def read_tdms(tdms_path):
    with TdmsFile.open(tdms_path) as tdms_file:
        # 获取所有组
        groups = tdms_file.groups()
        if not groups:
            raise ValueError("没有找到任何组")

        # 选择第一个组
        target_group = groups[0]
        # print(f"使用组名: {target_group.name}")

        # 获取所有通道
        channels = target_group.channels()
        if not channels:
            raise ValueError("组中没有通道")

        # 选择第一个通道
        target_channel = channels[0]
        # print(f"使用通道名: {target_channel.name}")

        rd_IQ = target_channel[:]
    N = len(rd_IQ)

    # 初始化 data_cp数组
    data_cp = np.zeros((N // 2, 2))

    # 填充 data_cp 数组
    j = 0
    for i in range(0, N, 2):
        data_cp[j, 0] = rd_IQ[i]
        data_cp[j, 1] = rd_IQ[i + 1]
        j += 1
    return data_cp


# 复信号写成IQ交织
def wr_cp_to_IQcross(data, ch):
    if ch == 0:
        # data为IQ两路信号
        wr_IQ_in = data
    else:
        # data为复信号
        wr_IQ_in = np.column_stack((np.real(data), np.imag(data)))
    N_wr_in = wr_IQ_in.shape[0]
    # 初始化 in_IQ数组
    in_IQ = np.zeros((N_wr_in * 2, 1))

    # 填充 in_IQ 数组
    j = 0
    for i in range(0, N_wr_in):
        in_IQ[j, 0] = wr_IQ_in[i, 0]
        in_IQ[j + 1, 0] = wr_IQ_in[i, 1]
        j += 2
    return in_IQ


# 二进制文件转txt文件
def convert_txt_func(data_name, dirin, dirout):
    # 配置参数
    data_zip = dirin + '/' + data_name + '.iq.tar'
    data_folder = dirout + '/' + data_name + '.iq'
    save_path = data_folder.replace('.iq', '.txt')

    # 解压 .tar 文件
    with tarfile.open(data_zip, 'r') as tar:
        tar.extractall(data_folder)

    # 获取所有 .float32 文件
    file_list = tf.io.gfile.glob((data_folder + '*.float32'))

    # 处理每个文件
    for file_path in file_list:
        try:
            # 读取二进制文件
            raw_data = tf.io.read_file(file_path)
            float32_data = tf.io.decode_raw(raw_data, tf.float32)

            # 转换为复数
            reshaped_data = tf.reshape(float32_data, [-1, 2])

            # 转换为实部虚部格式并保存
            np.savetxt(save_path, reshaped_data, fmt='%.6f %.6f')
            print(save_path)

        except Exception as e:
            print(f'Error processing {file_path}: {str(e)}')

    # 清理临时目录
    try:
        shutil.rmtree(data_folder)
    except Exception as e:
        print(f'Error removing directory: {str(e)}')

    print('Conversion completed!')
    return reshaped_data


# 对齐函数，计算偏移量并截断
def alignment_func(data_x, data_y):
    len_max = int(max(len(data_x), len(data_y)))
    # 数据尾部补0
    data_x_pad = np.pad(data_x, (0, len_max - len(data_x)), mode='constant')
    data_y_pad = np.pad(data_y, (0, len_max - len(data_y)), mode='constant')

    # 计算互相关值
    c = signal.correlate(data_x_pad, data_y_pad)
    idx = np.argmax(np.abs(c)) + 1
    bias = idx - len_max
    print('bias：{}'.format(bias))
    if bias <= 0:
        data_x_align = data_x
        data_y_align = data_y[-bias:]
    elif bias > 0:
        data_x_align = data_x[bias:]
        data_y_align = data_y
    len_min = int(min(len(data_x_align), len(data_y_align)))
    data_x_align_end = data_x_align[:len_min]
    data_y_align_end = data_y_align[:len_min]

    return data_x_align_end, data_y_align_end, bias


# 两个数据对齐
def alignment(data_x, data_y, fs, n, isplot):
    # 数据对齐
    data_x = data_x - np.mean(data_x)  # 去直流
    data_y = data_y - np.mean(data_y)

    data_x_coarse, data_y_coarse, bias_int = alignment_func(data_x, data_y)  # 整数对齐
    data_x_coarse, data_y_coarse = np.array(data_x_coarse), np.array(data_y_coarse)

    data_x_resample = signal.resample_poly(data_x_coarse, fs * n, fs)  # 上采样
    data_y_resample = signal.resample_poly(data_y_coarse, fs * n, fs)
    data_x_fine, data_y_fine, bias_dec = alignment_func(data_x_resample, data_y_resample)  # 小数对齐

    _, _, _ = alignment_func(data_x_fine, data_y_fine)  # 检测小数对齐

    data_x_final = signal.resample_poly(data_x_fine, fs, fs * n)  # 降采样
    data_y_final = signal.resample_poly(data_y_fine, fs, fs * n)
    _, _, _ = alignment_func(data_x_final, data_y_final)  # 检测整数对齐

    # 相位补偿
    pa_gain = data_y_final / data_x_final
    pha_shift = np.angle(pa_gain)
    pha_ema = np.zeros((len(pha_shift), 1))
    pha_ema[0] = pha_shift[0]
    delta = 0.01
    for i in range(1, len(pha_shift)):
        pha_ema[i] = delta * pha_shift[i] + (1 - delta) * pha_ema[i - 1]
    data_y_final = data_y_final * np.exp(-1j * pha_ema[-1])

    if isplot == 1:
        # 画图
        fig, axes = plt.subplots(2, 2, figsize=(20, 10))
        # AM/AM、AM/PM
        plot_nonlinear_func(data_x_final, data_y_final, axes[0, :])
        # 画时域两条曲线对比
        plot_delay_func(np.abs(data_x_final), np.abs(data_y_final), 2000, 200, axes[1, 0])
        # PSD
        plot_PSD_func(data_x_final, data_y_final, fs, axes[1, 1])
        plt.show()

    return data_x_final, data_y_final, bias_int, bias_dec


# 多个数据对齐
def multi_align(data, fs, n):
    M = len(data)
    N_in = len(data[0])
    bias_int = np.zeros((M, 1))
    bias_dec = np.zeros((M, 1))
    N_out = np.zeros((M - 1), dtype=int)
    for i in range(1, M):
        N_out[i - 1] = len(data[i])
        _, _, bias_int[i - 1], bias_dec[i - 1] = alignment(data[0], data[i], fs, n, 0)

    bias_int_p = int(max(bias_int))
    bias_int_n = int(min(bias_int))
    bias_dec_p = int(max(bias_dec))
    bias_dec_n = int(min(bias_dec))

    if bias_int_p <= 0:
        bias_int_p = 0
    if bias_int_n > 0:
        bias_int_n = 0

    if bias_dec_p <= 0:
        bias_dec_p = 0
    if bias_dec_n > 0:
        bias_dec_n = 0

    # 裁去整数头部
    data_in_int = data[0][bias_int_p:N_in]
    N_int = len(data_in_int)
    data_out_int = data[1][bias_int_p - int(bias_int[0]):N_out[0]]
    N_int = np.c_[N_int, len(data[1][bias_int_p - int(bias_int[0]):])]
    for i in range(2, M):
        data_out_int = [data_out_int, data[i][bias_int_p - int(bias_int[i - 1]):N_out[i - 1]]]  # 截断头部
        N_int = np.c_[N_int, len(data[i][bias_int_p - int(bias_int[i - 1]):N_out[i - 1]])]
    N_int_min = int(np.min(N_int, axis=1))

    # 裁去整数尾部
    data_in_int_end = data_in_int[:N_int_min]

    data_in_dec_upsample = signal.resample_poly(data_in_int_end, fs * n, fs)  # 上采样
    # 裁去小数头部
    data_in_dec = data_in_dec_upsample[bias_dec_p:]
    N_dec = len(data_in_dec)

    data_out_int_end = data_out_int[0][:N_int_min]  # 裁去整数尾部
    data_out_dec_upsample = signal.resample_poly(data_out_int_end, fs * n, fs)  # 上采样
    data_out_dec = data_out_dec_upsample[bias_dec_p - int(bias_dec[0]):]  # 截断小数头部
    N_dec = np.c_[N_dec, len(data_out_dec_upsample[bias_dec_p - int(bias_dec[0]):])]
    for i in range(1, M - 1):
        data_out_int_end = data_out_int[i][:N_int_min]  # 裁去整数尾部
        data_out_dec_upsample = signal.resample_poly(data_out_int_end, fs * n, fs)  # 上采样
        data_out_dec = [data_out_dec, data_out_dec_upsample[bias_dec_p - int(bias_dec[i]):]]  # 截断小数头部
        N_dec = np.c_[N_dec, len(data_out_dec_upsample[bias_dec_p - int(bias_dec[i]):])]
    N_dec_min = int(np.min(N_dec, axis=1))

    # 裁去小数尾部
    data_in_final = signal.resample_poly(data_in_dec[:N_dec_min], fs, fs * n)  # 降采样
    data_out_dec_end = signal.resample_poly(data_out_dec[0][:N_dec_min], fs, fs * n)  # 降采样
    # 相位补偿
    pa_gain = data_out_dec_end / data_in_final
    pha_shift = np.angle(pa_gain)
    pha_ema = np.zeros((len(pha_shift), 1))
    pha_ema[0] = pha_shift[0]
    delta = 0.01
    for i in range(1, len(pha_shift)):
        pha_ema[i] = delta * pha_shift[i] + (1 - delta) * pha_ema[i - 1]
    data_out_final = data_out_dec_end * np.exp(-1j * pha_ema[-1])

    for i in range(1, M - 1):
        data_out_dec_end = signal.resample_poly(data_out_dec[i][:N_dec_min], fs, fs * n)  # 降采样
        # 相位补偿
        pa_gain = data_out_dec_end / data_in_final
        pha_shift = np.angle(pa_gain)
        pha_ema = np.zeros((len(pha_shift), 1))
        pha_ema[0] = pha_shift[0]
        delta = 0.01
        for i in range(1, len(pha_shift)):
            pha_ema[i] = delta * pha_shift[i] + (1 - delta) * pha_ema[i - 1]
        data_out_final = [data_out_final, data_out_dec_end * np.exp(-1j * pha_ema[-1])]
        # in_select, out_select = find_best_NMSE(in_abs, out_abs, number_select)

    for i in range(M - 1):
        data_in_final_norm = data_in_final / np.max(data_in_final)
        data_out_final_norm = data_out_final[i] / np.max(data_out_final[i])
        # 画图
        fig, axes = plt.subplots(2, 2, figsize=(20, 10))
        # AM/AM、AM/PM
        plot_nonlinear_func(data_in_final_norm, data_out_final_norm, axes[0, :])
        # 画时域两条曲线对比
        plot_delay_func(np.abs(data_in_final_norm), np.abs(data_out_final_norm), 2000, 200, axes[1, 0])
        # PSD
        plot_PSD_func(data_in_final_norm, data_out_final_norm, fs, axes[1, 1])
        plt.show()
    return data_in_final, data_out_final


# 寻找最佳对齐段落
def find_best_NMSE(in_abs, out_abs, number_select):
    l = 1000
    L = len(out_abs) // l

    # 计算NMSE_ave_L
    NMSE_ave_L = np.zeros(L + 1)
    for i in range(L):
        NMSE_ave_L[i] = cal_NMSE_np(in_abs[i * l:(i + 1) * l], out_abs[i * l:(i + 1) * l])
    NMSE_ave_L[-1] = cal_NMSE_np(in_abs, out_abs)
    # print(NMSE_ave_L[-1])
    # 寻找最佳段
    min_y = np.min(NMSE_ave_L)
    # print(min_y)
    NMSE_select = 0
    max_longest_indices = []

    for NMSE_add in range(0, 10):
        is_greater = NMSE_ave_L <= (min_y + NMSE_add * 0.5)

        result = []
        indices = []
        current_segment = []
        current_indices = []
        longest_segment = []

        for i in range(len(NMSE_ave_L)):
            if is_greater[i] == 1:
                current_segment.append(NMSE_ave_L[i])
                current_indices.append(i)
            else:
                if current_segment:
                    result.append(current_segment)
                    indices.append(current_indices)
                    current_segment = []
                    current_indices = []

        if current_segment:
            result.append(current_segment)
            indices.append(current_indices)

        max_length = 0
        for k in range(len(result)):
            if len(result[k]) > max_length:
                max_length = len(result[k])
                longest_segment = result[k]
                longest_indices = indices[k]

        if number_select >= 12.288 * 1e4:
            if number_select < len(longest_segment) * l:
                if NMSE_select > np.mean(longest_segment):
                    NMSE_select = np.mean(longest_segment)
                    number_select = len(longest_segment) * l
                    max_longest_indices = longest_indices

    if (not max_longest_indices) or (NMSE_select >= NMSE_ave_L[-1]):
        in_select = in_abs
        out_select = out_abs
    else:
        in_select = in_abs[max_longest_indices[0] * l:max_longest_indices[-1] * l]
        out_select = out_abs[max_longest_indices[0] * l:max_longest_indices[-1] * l]
    # print(NMSE_select)
    print(f'NMSE_select={cal_NMSE_np(in_select, out_select)}; number_select={len(in_select)}')

    return in_select, out_select


# 保存文件
def save_txt_func(data, data_path):
    data_IQ = np.c_[data.real, data.imag]
    np.savetxt(data_path, data_IQ, fmt='%.8f')
    print(data_path)


# 计算功率
def cal_power(signal):
    """计算信号的功率 (dB)"""
    power = 10 * np.log10(np.mean(np.abs(signal) ** 2))
    return power


# 计算PAPR
def cal_PAPR(signal):
    """计算峰均功率比 (PAPR)"""
    signal_abs_sq = np.abs(signal) ** 2
    papr = 10 * np.log10(np.max(signal_abs_sq) / np.mean(signal_abs_sq))
    return papr


# 计算ACLR
def cal_ACLR(fs, data, main_bw, adj_offset):
    """
    计算主信道和邻信道的 ACLR
    :param f: 频率数组 (1D numpy array)
    :param data: 复信号 (1D numpy array，与f长度相同)
    :param fc: 中心频率 (Hz)
    :param main_bw: 主信道带宽 (Hz)
    :param adj_offset: 邻信道偏移量 (Hz)
    :param adj_bw: 邻信道带宽 (Hz)
    :return: ACLR_left (dB), ACLR_right (dB)
    """
    f, data_PSD = cal_PSD(data, fs)

    fc = 0 # 中心频率

    adj_bw = main_bw # 主信道带宽和邻信道带宽

    # 计算主信道平均功率（添加空数组检查）
    main_mask = (f >= fc - main_bw / 2) & (f <= fc + main_bw / 2)
    if np.any(main_mask):
        main_power = np.mean(data_PSD[main_mask])
    else:
        main_power = np.nan  # 或赋予默认值，如 0

    # 计算左侧邻信道平均功率
    adj_left_low = fc - adj_offset - adj_bw / 2
    adj_left_high = fc - adj_offset + adj_bw / 2
    adj_left_mask = (f >= adj_left_low) & (f <= adj_left_high)
    adj_left_power = np.mean(data_PSD[adj_left_mask])

    # 计算右侧邻信道平均功率
    adj_right_low = fc + adj_offset - adj_bw / 2
    adj_right_high = fc + adj_offset + adj_bw / 2
    adj_right_mask = (f >= adj_right_low) & (f <= adj_right_high)
    adj_right_power = np.mean(data_PSD[adj_right_mask])

    # 计算ACLR (单位dB)
    ACLR_left = adj_left_power - main_power
    ACLR_right = adj_right_power - main_power

    return ACLR_left, ACLR_right


# 计算NMSE
def cal_NMSE_np(y_true, y_pred):
    delta_sum = np.sum(np.square(np.abs(y_true - y_pred)))
    y_sum = np.sum(np.square(np.abs(y_true)))
    return 10 * np.log10(delta_sum / y_sum)

def cal_NMSE_tf(y_true, y_pred):
    numerator = tf.reduce_sum(tf.square(tf.abs(y_true - y_pred)))
    denominator = tf.reduce_sum(tf.square(tf.abs(y_true)))
    epsilon = tf.constant(1e-10, dtype=tf.float32)
    return 10 * tf.math.log(numerator / (denominator + epsilon)) / tf.math.log(10.0)


# 画AM/AM AM/PM
def plot_nonlinear_func(pa_in_c, pa_out_c, axes):
    # 功率曲线
    # 在第一个子图中绘制AM/AM图
    axes[0].scatter(20 * np.log10(abs(pa_in_c)), 20 * np.log10(np.abs(pa_out_c / pa_in_c)), 10)  # 原功放增益曲线，功率曲线
    # 辅助线
    x_vals = np.linspace(axes[0].get_xlim()[0], axes[0].get_xlim()[1], 100)
    y_vals = np.zeros_like(x_vals)  # 将 y_vals 设置为全零
    axes[0].plot(x_vals, y_vals, '--', color='black')
    axes[0].set_title('AM/AM', fontsize=20)
    axes[0].set_xlabel('输入功率/dBm', fontsize=20)
    axes[0].set_ylabel('功放增益/dB', fontsize=20)

    # 在第二个子图中绘制AM/PM图
    axes[1].scatter(20 * np.log10(abs(pa_in_c)), np.angle(pa_out_c / pa_in_c), 10)  # 预失真后相位变化曲线，功率曲线
    y_vals = np.zeros_like(x_vals)  # 将 y_vals 设置为全零
    axes[1].plot(x_vals, y_vals, '--', color='black')
    axes[1].set_title('AM/PM', fontsize=20)
    axes[1].set_xlabel('输入功率/dBm', fontsize=20)
    axes[1].set_ylabel('相位/rad', fontsize=20)

    # 幅值曲线
    # axes[0].scatter(np.abs(pa_in_c), np.abs(pa_out_c), 10)   #原功放输入-输出曲线，幅值曲线
    # 辅助线
    # x_vals = np.linspace(axes[0].get_xlim()[0],axes[0].get_xlim()[1], 100)
    # y_vals = np.zeros_like(x_vals)  # 将 y_vals 设置为全零
    # axes[0].plot(x_vals, y_vals, '--', color='black')
    # axes[0].set_title('AM/AM', fontsize=20)
    # axes[0].set_xlabel('输入幅值', fontsize=20)
    # axes[0].set_ylabel('输出幅值', fontsize=20)

    # axes[1].scatter(np.abs(pa_in_c), np.angle(pa_out_c/pa_in_c), 10) #原功放相位变化曲线，幅值曲线
    # y_vals = np.zeros_like(x_vals)  # 将 y_vals 设置为全零
    # axes[1].plot(x_vals, y_vals , '--', color='black')
    # axes[1].set_title('AM/PM', fontsize=20)
    # axes[1].set_xlabel('输入幅值', fontsize=20)
    # axes[1].set_ylabel('相位/rad', fontsize=20)


# 计算频谱图横纵坐标
def cal_PSD(x, fs, window_type='kaiser', nfft=512, step=128):
    """
        计算信号的功率谱密度 (Power Spectral Density, PSD)

        参数:
        - x: 输入信号，形状为 (N,)，一个时间序列。
        - fs: 采样频率，默认为 1.0 Hz。
        - window_type: 窗函数的类型，默认为 'hamming'。
        - nfft: FFT 的点数。如果为 None，则为信号长度。
        - step: 每次滑动窗口的步长。

        返回:
        - f: 频率轴的值。
        - psd: 计算得到的功率谱密度。
        """
    # 应用窗函数
    if window_type == 'hamming':
        window = np.hamming(nfft)
    elif window_type == 'hanning':
        window = np.hanning(nfft)
    elif window_type == 'blackman':
        window = np.blackman(nfft)
    elif window_type == 'kaiser':
        window = kaiser(nfft, beta=20)
    else:
        raise ValueError(f"Unsupported window type: {window_type}")

    # 将信号分成多个重叠的窗口段
    segments = []
    for start in range(0, len(x) - nfft + 1, step):
        segment = x[start:start + nfft]
        segments.append(segment * window)  # 应用窗函数

    # 计算每个窗口的 FFT 并取幅度平方得到功率谱
    fft_results = []
    for segment in segments:
        fft_res = tf.signal.fft(tf.cast(segment, tf.complex64))  # FFT
        fft_mag_squared = tf.square(tf.abs(fft_res))  # 幅度平方
        fft_results.append(fft_mag_squared)

    # 将每个窗口的频谱结果取平均，得到最终的功率谱密度
    psd = tf.reduce_mean(tf.stack(fft_results), axis=0)  # 按频率平均
    psd = psd / fs  # 归一化功率谱密度

    # # 手动计算频率轴
    freqs = np.fft.fftfreq(nfft, d=1 / fs)  # 使用 numpy 来计算频率轴
    # print(freqs.shape)
    freqs = np.fft.fftshift(freqs)  # 调整频率范围，从 -fs/2 到 fs/2
    # 对应的 PSD 数据也需要通过 fftshift 来调整
    psd = np.fft.fftshift(psd)  # 调整 PSD，使其对应于从 -fs/2 到 fs/2 的频率

    psd_db = 10 * tf.math.log(psd) / tf.math.log(10.0)

    return freqs, psd_db


# 计算画PSD的横纵坐标
def plot_PSD_func(x, y, fs, axes, window_type='kaiser', nfft=512, step=128):
    freq_x, psd_x = cal_PSD(x, fs)
    freq_y, psd_y = cal_PSD(y, fs)
    # 绘制结果
    axes.plot(freq_x / 1e6, psd_x, linestyle='-', label='original')
    axes.plot(freq_y / 1e6, psd_y, linestyle='-', label='fitting')
    axes.set_title('Power Spectral Density (PSD) in dB')
    axes.set_xlabel('Frequency [MHz]')
    axes.set_ylabel('Power [dB]')
    plt.grid(True)
    # plt.xlim([-fs / 1e6 / 2, fs / 1e6 / 2])
    # plt.ylim([-120, -60])


# 画时域两条曲线对比
def plot_delay_func(data_x_final, data_y_final, t, delay, axes):
    axes.plot(range(t, t + delay), data_x_final[t:t + delay], 'r', label='INPUT')
    axes.plot(range(t, t + delay), data_y_final[t:t + delay], 'g', label='OUTPUT')
    axes.set_xlabel('Samples')
    axes.set_ylabel('Normalized Amplitude')
    axes.set_title('Delay Alignment')


# 记忆深度表达
def memory_depth_func(data, mem_depth):
    data_with_mem = []
    for i in range(0, len(data) - mem_depth):
        x = data[i:i + mem_depth, :]
        data_with_mem.append(x)
    return data_with_mem


# 定义最佳模型检查点
callbacks = [
    # 自动调节学习速率，监测loss，每三个点学习速率改为原来的0.5(超参数)，最小为1e-6
    tf.keras.callbacks.ReduceLROnPlateau('val_loss', patience=3, factor=0.5, min_lr=1e-6)]
