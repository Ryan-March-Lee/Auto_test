# DPD 子系统边界

`DPD_auto_test/` 是独立的数字预失真实验子系统，不属于 PA 主测量链路。

## 输入与输出

- 输入：由 notebook 或 DPD 脚本显式指定的复数基带采样数据、采样率和模型参数。
- 输出：调用者指定目录中的系数、预失真采样数据和分析文件。
- 默认情况下不读取或修改主系统的 `config.json`、`test_results/`、`baseline/`、运行快照或日志。
- 主系统结果不能作为隐式输入；需要交换数据时使用调用者明确传入的脱敏文件路径。

## 依赖与启动

DPD 依赖单独记录在 `requirements-dpd.txt`。主系统安装和启动不需要这些依赖。

该清单覆盖脚本和 notebook 使用的 NumPy、Matplotlib、SciPy、TensorFlow、
nptdms、PyVISA 和 Jupyter。`RsSmw` 是仪器厂商/实验室本地驱动，不在 PyPI
发布，必须由实验室单独提供并放入 DPD 运行环境。

建议从本目录启动 notebook 或脚本，并显式传入输入输出路径。未经单独决策，不进行目录迁移，也不从主系统模块导入配置和测量服务。
