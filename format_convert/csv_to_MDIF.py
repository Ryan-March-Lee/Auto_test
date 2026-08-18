import pandas as pd

# 读取CSV文件 - 使用逗号分隔符
df = pd.read_csv('D:/All_Projects/VsCode_projects/pa_auto_test/test_results/20260408_104612/full_sweep_data.csv', 
                 sep=',',            # 改为逗号分隔
                 encoding='utf-8',
                 engine='python')

# 打印调试信息
print("=" * 70)
print("文件读取结果:")
print("=" * 70)
print(f"行数: {len(df)}")
print(f"列数: {len(df.columns)}")

print("\n列名:")
for i, col in enumerate(df.columns):
    print(f"  [{i:2d}] {col}")

print("\n前5行数据:")
print(df.head())

print("\n数据统计:")
print(df[['input_power_dut', 'output_power_dut', 'gain', 'efficiency', 'frequency_ghz']].describe())

# 获取唯一频率
frequencies = sorted(df['frequency_ghz'].unique())
print(f"\n找到 {len(frequencies)} 个频点:")
print(frequencies)

# 每个频点的数据量
print("\n各频点数据量:")
for freq in frequencies:
    count = len(df[df['frequency_ghz'] == freq])
    print(f"  {freq} GHz: {count} 个数据点")

# 生成MDIF文件
output_file = 'power_data.mdf'
with open(output_file, 'w', encoding='utf-8') as f:
    f.write('!============================================================\n')
    f.write('! Power Amplifier Measurement Data\n')
    f.write('! Source: full_sweep_data.csv\n')
    f.write(f'! Date: 2025-10-15\n')
    f.write(f'! Total frequencies: {len(frequencies)}\n')
    f.write(f'! Total data points: {len(df)}\n')
    f.write('!============================================================\n\n')
    
    for freq in frequencies:
        df_freq = df[df['frequency_ghz'] == freq].copy()
        
        # 按输入功率排序
        df_freq = df_freq.sort_values('input_power_dut')
        
        print(f"\n处理 {freq} GHz: {len(df_freq)} 个数据点")
        
        f.write(f'VAR frequency_ghz(real) = {freq}\n')
        f.write('BEGIN AMP_DATA\n')
        f.write('% input_power_dut(real) output_power_dut(real) gain(real) ')
        f.write('dc_power(real) efficiency(real)\n')
        
        for _, row in df_freq.iterrows():
            f.write(f"{row['input_power_dut']:.3f} ")
            f.write(f"{row['output_power_dut']:.3f} ")
            f.write(f"{row['gain']:.2f} ")
            f.write(f"{row['dc_power']:.8f} ")
            f.write(f"{row['efficiency']:.9f}\n")
        
        f.write('END\n\n')

print("\n" + "=" * 70)
print(f"✅ 转换完成: {output_file}")
print(f"   文件位置: D:/All_Projects/VsCode_projects/pa_auto_test/{output_file}")
print("=" * 70)

# 验证生成的文件
import os
if os.path.exists(output_file):
    file_size = os.path.getsize(output_file)
    print(f"\n文件大小: {file_size:,} 字节")
    print(f"预览前20行:")
    with open(output_file, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f):
            if i < 20:
                print(f"  {line.rstrip()}")
            else:
                break