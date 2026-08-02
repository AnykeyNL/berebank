const OUTCOMES = ['up', 'sideways', 'down'] as const

export function largestRemainderPercentages(
  probabilities: unknown,
): Record<(typeof OUTCOMES)[number], number> | null {
  if (!probabilities || typeof probabilities !== 'object') return null
  const record = probabilities as Record<string, unknown>
  const values = OUTCOMES.map((key) => {
    const raw = record[key]
    if (typeof raw !== 'string' && typeof raw !== 'number') return null
    const parsed = Number(raw)
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : null
  })
  if (values.some((value) => value === null)) return null
  const finiteValues = values as number[]
  const total = finiteValues.reduce((sum, value) => sum + value, 0)
  if (!Number.isFinite(total) || total <= 0 || Math.abs(total - 1) > 0.000001) return null

  const exact = finiteValues.map((value) => (value / total) * 100)
  const rounded = exact.map(Math.floor)
  const remaining = 100 - rounded.reduce((sum, value) => sum + value, 0)
  const order = exact
    .map((value, index) => ({ index, remainder: value - rounded[index] }))
    .sort((a, b) => b.remainder - a.remainder || a.index - b.index)
  for (let index = 0; index < remaining; index += 1) {
    rounded[order[index % order.length].index] += 1
  }
  return { up: rounded[0], sideways: rounded[1], down: rounded[2] }
}
