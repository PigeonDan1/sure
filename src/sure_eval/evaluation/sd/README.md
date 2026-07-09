# SD Metric

Speaker Diarization metrics use RTTM reference and hypothesis files.

The DER calculation loads RTTM files with `meeteval.io.load` and scores them with `meeteval.der.dscore`.

The default collar is `0.25`.

The output schema contains `der` and `num_sessions`.

Per-session missed, false alarm, and speaker error values are printed only for inspection and do not change the return schema.
