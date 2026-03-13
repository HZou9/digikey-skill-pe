# DigiKey Skills - TODO List

## 高优先级 (High Priority)

### 1. 注册 DigiKey API 凭证
- [ ] 在 https://developer.digikey.com 注册开发者账号
- [ ] 创建应用，获取 Client ID + Client Secret
- [ ] 先用 Sandbox 环境测试（设置 `DIGIKEY_USE_SANDBOX=true`）
- [ ] 将凭证填入 `digikey-skill/.env`（从 `.env.template` 复制）
- **注意**: 免费 API 限制 1000 次/天

### 2. 注册 Nexar API (符号/封装下载)
- [ ] 在 https://nexar.com/api 免费注册
- [ ] 创建应用 → 获取 Client ID + Secret
- [ ] 填入 `digikey-skill-pe/.env` 的 `NEXAR_CLIENT_ID` 和 `NEXAR_CLIENT_SECRET`
- **用途**: 自动下载 KiCad 9 + Altium Designer 的原理图符号和 PCB 封装

### 3. 从 Mock 模式切换到真实 API
- [ ] 配好凭证后运行 `python3 scripts/search.py search "SiC MOSFET 650V"` 验证
- [ ] 确认真实 API 返回的数据格式与 mock 兼容
- [ ] 测试 OAuth2 token 的刷新机制

## 中优先级 (Medium Priority)

### 4. Datasheet PDF Parser 改进
- [x] 支持 ROHM 格式（换行分割符号: V\nDSS → VDSS）
- [x] 支持 TI 格式（Min/Typ/Max 合并在单列）
- [x] 修复厂商检测优先级（TI 被误判为 Wolfspeed）
- [ ] Wolfspeed: Vds_max 偶尔从绝对最大值表提取失败
- [ ] Wolfspeed: Id_max 匹配到脉冲电流而非连续电流
- [ ] 支持更多厂商格式：Infineon, onsemi, STMicroelectronics
- [x] 添加 Qoss 提取/估算（从 Eoss 估算: Qoss ≈ 2×Eoss/Vds）
- [ ] 电容器 datasheet 支持（MLCC: capacitance vs DC bias）

### 5. BOM 优化增强
- [ ] 添加交叉参考（cross-reference）：在 DigiKey 搜不到时自动搜 Mouser
- [ ] 批量定价阶梯比较
- [x] 库存告警：标记库存不足的器件
- [x] BOM 导出到 CSV

### 6. Gate Driver 选型改进
- [ ] 根据实际 MOSFET 参数自动推荐 gate driver
- [x] 添加 dead-time 计算和推荐电阻值（Rg_on/Rg_off, E24 标准值）
- [x] Bootstrap 电容选型（buck/boost 拓扑）

## 低优先级 (Low Priority)

### 7. 更多拓扑支持
- [x] 三电平 NPC / T-type 变换器
- [x] CLLC 谐振变换器
- [x] PFC (Power Factor Correction) 级
- [ ] 无线充电 (WPT) 相关器件

### 8. 大功率模块支持 (Power Modules)
- [x] 添加 power module 搜索模板（IGBT module, SiC module）
- [x] 添加 power module mock 数据（Wolfspeed CAB450M12XM3, Infineon FF450R12ME4, etc.）
- [x] 散热器选型集成（RthSA 计算 → 散热器推荐）
- [x] 散热器 mock 数据（风冷铝散热器 + 液冷板）
- [x] `power-module` 和 `heatsink` CLI 命令
- [ ] 热仿真接口（导出到 PLECS/LTspice）
- **注意**: DigiKey API 可以搜索到 power module，需要添加特定参数解析

### 9. 工具集成
- [x] 生成 BOM 到 CSV 导出（`--csv` 标志）
- [x] MOSFET 选型结果导出到 CSV
- [ ] 与 KiCad 9 项目文件集成（直接导入符号/封装）
- [ ] 批量 datasheet 下载和比较

### 10. 自动化测试
- [x] 单元测试：FOM 计算、Loss estimation、Datasheet parser
- [x] 集成测试：完整选型流程（mock API）
- [x] 新增测试：Gate resistor, Bootstrap cap, BOM optimizer, CSV export, Topology weights
- [x] 回归测试：76 项测试全部通过

## 已完成 (Completed)

- [x] DigiKey API 客户端（OAuth2 认证 + mock 模式）
- [x] 关键字搜索、产品详情、定价、替代品查询
- [x] MOSFET FOM 计算引擎（Rds×Qg, Rds×Qoss, FOM/$）
- [x] 拓扑感知权重（DAB, LLC, CLLC, Buck, Boost, Full Bridge, NPC, T-type, PFC）
- [x] 损耗估算（导通损耗 + 开关损耗 + 栅极损耗）
- [x] PDF datasheet 解析器（pdfplumber, 无需 LLM）
- [x] Gate driver 需求计算（从 MOSFET Qg 推导）
- [x] Gate resistor 计算（Rg_on/Rg_off, E24 标准值, 开关时间估算）
- [x] Bootstrap 电容计算（Cboot 最小值 + 推荐值 + 纹波分析）
- [x] BOM 优化器（定价分析 + 二供查找 + 库存告警）
- [x] BOM / MOSFET 选型 CSV 导出
- [x] 符号/封装下载架构（Nexar + easyeda2kicad + 手动链接回退）
- [x] Claude Code Skill 配置（SKILL.md）
- [x] 76 项单元测试 + 集成测试全部通过
- [x] GitHub 备份上传
