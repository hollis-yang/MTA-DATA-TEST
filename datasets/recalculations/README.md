# Saved ETA recalculations

These are existing local analysis outputs, preserved without changing their values. Read each `summary.json` for the exact method, source hashes, exclusions, and available collection windows.

- `eta_vehicle_9_11_f_includes_fx/`: three-window comparison using original vehicle-derived arrival candidates, a 9–11 minute predicted-wait reference, and F including FX. `three_window_summary.json` combines results and official notices.
- `eta_vehicle_9_11_recalculation/`: earlier 9–11 minute reference results before combined F/FX reporting.
- `eta_closest_zero_recalculation/`: alternative near-zero ETA endpoint estimates; includes intermediate and final variants.
- `eta_0_30_recalculation/`: alternative 0–30 second ETA endpoint estimates.

These methods are not interchangeable. Near-zero ETA endpoints assume accuracy rather than establish actual arrival. Vehicle-derived arrivals also remain unvalidated against physical arrivals. Historical absolute local paths identify source files; retrieve the corresponding original runs from the collection archives.

Recalculation source is in `scripts/recalculate_arrival_reference.py` and `scripts/recalculate_eta_near_arrival.py` at repository root.
