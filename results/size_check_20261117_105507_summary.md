# Size-order check: size_check_20261117_105507.csv

Compared with the main run `run_20261117_103103_t10.csv` (large sizes always last in each round). In the check, every (size, config) pair ran once per round in a shuffled order with a cooldown before every trial (see the `_run.json` file).

| Config | Size | Main run: MB/s (± 95% CI) | Main: % of 256 KB | Check: MB/s (± 95% CI) | Check: % of 256 KB | n (check) |
|---|---|---|---|---|---|---|
| aesni_on | 256 KB | 6,693.8 ± 59.1 | 100.0% | 6,587.5 ± 691.1 | 100.0% | 5 |
| aesni_on | 64 MB | 6,039.5 ± 126.7 | 90.2% | 6,643.9 ± 100.1 | 100.9% | 5 |
| aesni_on | 1 GB | 5,961.1 ± 154.6 | 89.1% | 6,295.7 ± 197.2 | 95.6% | 5 |
| aesni_off | 256 KB | 169.0 ± 4.2 | 100.0% | 183.7 ± 1.6 | 100.0% | 5 |
| aesni_off | 64 MB | 165.3 ± 4.6 | 97.8% | 181.1 ± 3.3 | 98.6% | 5 |
| aesni_off | 1 GB | 152.2 ± 1.7 | 90.0% | 180.5 ± 3.7 | 98.2% | 5 |
| chacha_on | 256 KB | 2,009.0 ± 27.5 | 100.0% | 2,051.8 ± 37.0 | 100.0% | 5 |
| chacha_on | 64 MB | 1,759.0 ± 60.9 | 87.6% | 2,012.1 ± 41.6 | 98.1% | 5 |
| chacha_on | 1 GB | 1,708.1 ± 45.0 | 85.0% | 1,946.7 ± 83.7 | 94.9% | 5 |

## Effective clock during each trial (check run)

Windows `% Processor Performance` = effective clock as % of the 4.0 GHz base; we take the busiest logical processor each second and average over the trial. Approximate (1 sample per second).

| Config | Size | Mean clock % of base | Min | Max |
|---|---|---|---|---|
| aesni_on | 256 KB | 60.7 | 60.4 | 61.0 |
| aesni_on | 64 MB | 60.5 | 59.5 | 61.0 |
| aesni_on | 1 GB | 61.4 | 61.2 | 61.4 |
| aesni_off | 256 KB | 60.2 | 59.5 | 60.6 |
| aesni_off | 64 MB | 60.5 | 59.5 | 61.2 |
| aesni_off | 1 GB | 61.1 | 60.9 | 61.2 |
| chacha_on | 256 KB | 60.7 | 60.4 | 61.1 |
| chacha_on | 64 MB | 60.4 | 58.8 | 60.8 |
| chacha_on | 1 GB | 61.4 | 61.3 | 61.5 |
