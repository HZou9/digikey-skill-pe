# DigiKey Skill PE - TODO List

## 已完成 (Completed)

- [x] DigiKey Production API 连通 + OAuth2 认证
- [x] API 响应规范化（真实 API ↔ mock 字段映射）
- [x] MOSFET 搜索 + FOM 排序（多查询 + 多电压档 + 厂商多样性）
- [x] Gate driver 搜索（参数化多电流档搜索 + 厂商多样性）
- [x] Gate driver 需求推导（Qg → Io/dead-time/Vcc）
- [x] Gate resistor 计算（Rg_on/Rg_off, E24, SiC 多种关断选项）
- [x] Bootstrap 电容计算
- [x] 功率模块搜索 + 散热器搜索
- [x] 电容选型（dc_link/resonant/filter/emi_x/emi_y/snubber/bootstrap + 电压过滤）
- [x] 磁性元件搜索（ferrite/nanocrystalline/inductor/PFC/transformer/bobbin/CMC/EMI）
- [x] 器件交叉参考（DigiKey substitutions + 参数化搜索）
- [x] BOM 成本 + 库存查询 + CSV 导出
- [x] Datasheet PDF 解析器（已验证: ST, Infineon, ROHM, TI, Wolfspeed）
- [x] KiCad footprint 下载（easyeda2kicad, MPN → JLCPCB → LCSC ID, 免费）
- [x] FOM 计算（Rds×Qg, Rds×Qoss, FOM/$）— 仅参考排序
- [x] 拓扑权重（DAB/LLC/CLLC/Buck/Boost/FB/NPC/T-type/PFC）
- [x] 76 项测试全部通过
- [x] 两个 skill 独立可运行
- [x] SKILL.md 完整文档
- [x] GitHub 推送

## 待完成 (TODO)

### 中优先级
- [ ] KiCad 库表自动挂载（编辑 sym-lib-table / fp-lib-table）
- [ ] 电容器 datasheet 解析（DC bias 曲线、ESR vs freq）
- [ ] Wolfspeed parser 小修: Vds_max 偶尔从绝对最大值表提取失败
- [ ] Wolfspeed parser 小修: Id_max 匹配到脉冲电流而非连续电流
- [ ] Eon/Eoff parser bug: ST datasheet 中 Eon 误匹配到 Rds_on 值

### 低优先级
- [ ] WPT 无线充电拓扑支持
- [ ] 热仿真接口（导出到 PLECS/LTspice）
- [ ] 批量 datasheet 下载和比较
- [ ] Mouser API 集成（DigiKey 搜不到时备选）
