export const DEFAULT_COMBINATION_BATCH_SIZE = 10;

export function combinationWindow(items, visibleCount = DEFAULT_COMBINATION_BATCH_SIZE) {
  const source = Array.isArray(items) ? items : [];
  const boundedCount = Math.max(0, Math.min(Number(visibleCount) || 0, source.length));
  return {
    visible: source.slice(0, boundedCount),
    remaining: source.length - boundedCount,
    hasMore: boundedCount < source.length,
  };
}

export function nextCombinationWindowSize(
  visibleCount,
  totalCount,
  batchSize = DEFAULT_COMBINATION_BATCH_SIZE,
) {
  return Math.min(totalCount, Math.max(0, visibleCount) + batchSize);
}
