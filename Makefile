# XDMA top-level targets. Run `source tools/env.sh` first (see docs/ENV.md).
#
#   make lint                       Verilator -Wall lint + slang elaboration
#   make sim [TEST=t] [SEED=n] [WAVES=1] [PARAMS=K=V,..]
#   make regress [SEEDS=200]        every random test across N seeds
#   make cov                        line/toggle/assertion + functional coverage
#   make formal                     every SymbiYosys proof, summary table
#   make syn                        Yosys + OpenSTA on ASAP7: Fmax, area, power
#   make gls                        gate-level sim of test_loopback_copy
#   make perf                       performance sweeps, CSV + PNG
#   make results                    regenerate results/RESULTS.md

PY      ?= python3
SEEDS   ?= 200
JOBS    ?= 4
TEST    ?=
SEED    ?=
WAVES   ?= 0
PARAMS  ?=

SIM_ARGS := $(if $(TEST),--test $(TEST)) $(if $(SEED),--seed $(SEED)) \
            $(if $(filter 1,$(WAVES)),--waves) $(if $(PARAMS),--params $(PARAMS))

.PHONY: lint sim regress cov formal syn gls perf results clean

lint:
	./tools/lint.sh

sim:
	$(PY) tb/run.py $(SIM_ARGS)

regress:
	$(PY) tb/regress.py --seeds $(SEEDS) --jobs $(JOBS)

cov:
	$(PY) tb/coverage.py --jobs $(JOBS)

formal:
	$(PY) formal/run_formal.py

syn:
	$(PY) syn/yosys/run_syn.py

gls:
	$(PY) tools/run_gls.py --seed $(or $(SEED),1)

perf:
	$(PY) perf/run_perf.py --jobs $(JOBS)

results:
	$(PY) tools/make_results.py

clean:
	rm -rf tb/build formal/out perf/build perf/out syn/out
	find formal/sby -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} +
