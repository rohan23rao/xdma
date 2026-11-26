# OpenSTA: timing (and optionally power) for the Yosys netlist.
# Environment: NETLIST, PERIOD (ps), LIBDIR, OUTDIR, TAG, and optionally
# ACT (SAIF activity file) with ACT_SCOPE (its instance path to the DUT).
set libdir $::env(LIBDIR)
foreach f [glob $libdir/*.lib] { read_liberty $f }
read_verilog $::env(NETLIST)
link_design xdma_top

set period $::env(PERIOD)
create_clock -name clk -period $period [get_ports clk]
set in_ports [delete_from_list [all_inputs] [get_ports clk]]
# I/O delays: 20% of the period (spec 17.2)
set_input_delay  [expr {0.2 * $period}] -clock clk $in_ports
set_output_delay [expr {0.2 * $period}] -clock clk [all_outputs]
# Inputs driven by a BUFx2, outputs loaded by four BUFx2 inputs
set_driving_cell -lib_cell BUFx2_ASAP7_75t_R -pin Y $in_ports
set buf_cap [get_property [get_lib_pins */BUFx2_ASAP7_75t_R/A] capacitance]
set_load [expr {4.0 * $buf_cap}] [all_outputs]

set out $::env(OUTDIR)
set tag $::env(TAG)
report_checks -path_delay max -group_path_count 10 -fields {slew cap input_pins} -digits 1 > $out/timing_paths_$tag.rpt
set wns [sta::worst_slack -max]
set tns [sta::total_negative_slack -max]
set f [open $out/timing_$tag.txt w]
puts $f "period_ps $period"
puts $f "wns_ps $wns"
puts $f "tns_ps $tns"
close $f

if {[info exists ::env(ACT)] && $::env(ACT) ne ""} {
  read_saif -scope $::env(ACT_SCOPE) $::env(ACT)
  report_activity_annotation > $out/activity_$tag.rpt
  report_power -digits 4 > $out/power_$tag.rpt
}
exit
