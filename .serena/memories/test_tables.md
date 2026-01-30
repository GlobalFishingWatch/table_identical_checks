# Test Tables for Comparison

The user frequently uses these two tables for testing table comparisons:

## Table A (Reference)
`world-fishing-827.pipe_ais_test_202408290000_internal.research_messages`

## Table B (Development)
`world-fishing-827.scratch_christian_homberg_ttl120d._dev_research_messages_20200101_20201230`

## Characteristics
- Both tables have ~8M rows
- 26 columns total
- Key column: `msgid`
- Previously identified 61,953 differences between them
- All column types now supported: INT64, FLOAT64, STRING, TIMESTAMP, BOOLEAN

## Previous Comparison Results
When comparing without tolerance:
- 61,953 total differences found
- 24/26 columns successfully compared (before TIMESTAMP and BOOLEAN support was added)
- Now all 26/26 columns can be compared

## Typical Usage
```bash
table-check summary \
  --table-a=world-fishing-827.pipe_ais_test_202408290000_internal.research_messages \
  --table-b=world-fishing-827.scratch_christian_homberg_ttl120d._dev_research_messages_20200101_20201230 \
  --keys=msgid \
  --tolerance=1e-9
```
