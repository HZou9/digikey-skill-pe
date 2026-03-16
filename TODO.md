# DigiKey Skill PE - TODO List

## 已完成 (Completed)

- [x] DigiKey API 客户端（OAuth2 + mock 模式 + 自动切换）
- [x] DigiKey Production API 连通测试
- [x] API 响应规范化（真实 API ↔ mock 字段自动映射）
- [x] MOSFET 搜索 + FOM 排序（多查询 + 厂商多样性）
- [x] Gate driver 搜索（参数化多电流档搜索，不硬编码品牌）
- [x] Gate driver 需求推导（从 MOSFET Qg → Io/dead-time/Vcc）
- [x] Gate resistor 计算（Rg_on/Rg_off, E24, SiC -5V/-2V/0V+AMC）
- [x] Bootstrap 电容计算
- [x] 功率模块搜索 + 散热器搜索
- [x] 电容选型（dc_link/resonant/filter/emi_x/emi_y/snubber/bootstrap）
- [x] 电容电压过滤（低于要求电压的自动排除）
- [x] 磁性元件搜索（ferrite/nanocrystalline/inductor/PFC/transformer/bobbin/CMC/EMI filter）
- [x] 器件交叉参考（DigiKey substitutions + 参数化搜索）
- [x] BOM 成本 + 库存查询 + CSV 导出
- [x] Datasheet PDF 解析器（ROHM/TI/Wolfspeed 格式）
- [x] easyeda2kicad 封装下载（MPN → JLCPCB API → LCSC ID → KiCad files）
- [x] FOM 计算（Rds×Qg, Rds×Qoss, FOM/$）— 仅参考，不做损耗排序
- [x] 拓扑权重（DAB/LLC/CLLC/Buck/Boost/FB/NPC/T-type/PFC）
- [x] 76 项测试全部通过
- [x] 两个 skill 独立可运行（各自内嵌 digikey_api/）
- [x] GitHub 推送（digikey-skill + digikey-skill-pe）

## 待完成 (TODO)

### 中优先级

- [ ] KiCad 库表自动挂载（编辑 sym-lib-table / fp-lib-table）
- [ ] Datasheet parser 更多厂商：Infineon, onsemi, ST 格式
- [ ] Wolfspeed parser: Vds_max 偶尔从绝对最大值表提取失败
- [ ] Wolfspeed parser: Id_max 匹配到脉冲电流而非连续电流
- [ ] 电容器 datasheet 解析（MLCC capacitance vs DC bias）
- [ ] SKILL.md 更新（新增 capacitor/magnetics/xref 命令说明）

### 低优先级

- [ ] WPT 无线充电拓扑支持
- [ ] 热仿真接口（导出到 PLECS/LTspice）
- [ ] 批量 datasheet 下载和比较
- [ ] Mouser API 集成（DigiKey 搜不到时备选）
