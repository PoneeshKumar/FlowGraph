"""
FlowGraph benchmarking harness.

Validates the cycle detection engine against labeled external datasets.

IBM AML (primary):
    python -m benchmarks.ibm_aml.runner \\
        --csv  benchmarks/data/HI-Small_Trans.csv \\
        --patterns benchmarks/data/HI-Small_Patterns.txt \\
        --neo4j-only

Dataset acquisition:
    Download HI-Small_Trans.csv and HI-Small_Patterns.txt from
    https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml
    and place them in benchmarks/data/ (gitignored).
"""
