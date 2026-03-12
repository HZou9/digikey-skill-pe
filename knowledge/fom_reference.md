# Power Electronics FOM Reference

## MOSFET Figures of Merit

### Rds(on) × Qg [mΩ·nC]
- **Most common FOM**, good for general comparison at same voltage rating
- Lower = better balance of conduction and switching performance
- **Best for**: Hard-switched topologies (Buck, Boost, Flyback)
- Typical ranges by voltage class:
  - 100V: 50-500 mΩ·nC
  - 650V Si: 2000-5000 mΩ·nC
  - 650V SiC: 1000-3000 mΩ·nC
  - 1200V SiC: 3000-8000 mΩ·nC

### Rds(on) × Qoss [mΩ·nC]
- **Critical for soft-switching** (ZVS/ZCS) topologies
- Output charge determines energy stored in Coss → affects ZVS transitions
- **Best for**: DAB, LLC, phase-shifted full-bridge
- SiC advantage: Qoss much lower than Si at same Rds(on)

### Rds(on) × Coss [mΩ·pF]
- Related to Rds×Qoss but uses small-signal capacitance
- Less accurate than Qoss-based FOM (Coss is voltage-dependent)
- Useful when Qoss data not available

### FOM per Dollar [mΩ·nC/$]
- FOM divided by unit price → cost-effectiveness metric
- Important for production BOM optimization
- SiC has higher $/FOM but better at system level (smaller magnetics)

## Loss Estimation

### Conduction Loss
P_cond = I²_rms × Rds(on)
- Temperature dependent: Rds(on) increases ~1.5-2x at 150°C (Si), ~1.2-1.5x (SiC)
- For bridge topologies: each FET conducts during its half-cycle

### Switching Loss (Hard-switched)
P_sw ≈ ½ × Vds × Id × (tr + tf) × Fsw + ½ × Coss × Vds² × Fsw
- Turn-on: current rises while voltage still high
- Turn-off: voltage rises while current still flowing
- Reverse recovery of body diode adds to turn-on loss

### Switching Loss (Soft-switched / ZVS)
P_sw_zvs ≈ Eoss × Fsw (turn-on loss nearly zero if ZVS achieved)
- ZVS eliminates turn-on switching loss
- Remaining loss: output capacitance energy (Eoss)
- ZVS condition: magnetizing current must charge/discharge Coss in dead time

### Gate Drive Loss
P_gate = Qg × Vgs × Fsw
- Independent of load current
- Significant at high Fsw (>200kHz) with high-Qg devices
- SiC at 18V: P_gate = 95nC × 18V × 100kHz = 0.17W per device

## Topology Selection Guide

### DAB (Dual Active Bridge)
- **Primary side**: High voltage (400-800V), needs SiC for >600V
- **Secondary side**: Lower voltage, can use Si or SiC
- **Gate drivers**: Always isolated (4 independent half-bridges)
- **Key FOM**: Rds×Qoss (ZVS operation)
- **Fsw range**: 50-300kHz typical

### LLC Resonant
- **Similar to DAB** but unidirectional
- Inherent ZVS for primary FETs
- **Key FOM**: Rds×Qoss
- **Gate drivers**: Isolated for primary, can be bootstrap for secondary SR

### Buck/Boost
- **Hard-switched** → gate charge dominant
- **Key FOM**: Rds×Qg
- **High-side driver**: Bootstrap (buck) or isolated
- **Sync rect**: Same FOM considerations

## Derating Rules

### Voltage Derating
- Standard: 80% of Vds_max (i.e., 650V FET for 520V max)
- Conservative: 70% (e.g., 650V for 455V)
- Automotive: 60-70% typical

### Thermal Derating
- Tj_max = 150°C (Si), 175°C (SiC) — derate to 80% for margin
- Rds(on) at operating temp = Rds(on)_25C × (1 + α × ΔT)
  - α ≈ 0.004/°C for Si, ≈ 0.002/°C for SiC

### Capacitor Derating
- Ceramic (MLCC): Voltage derate 50% (capacitance drops ~30% at rated V)
- Electrolytic: Derate 20% voltage, watch ripple current rating
- Film: Derate 10-20% voltage
