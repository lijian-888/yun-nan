function aborted(signal) {
  if (!signal?.aborted) return;
  const error = new Error("请求已取消");
  error.name = "AbortError";
  throw error;
}

function wait(delayMs, signal) {
  if (!delayMs) {
    aborted(signal);
    return Promise.resolve();
  }
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, delayMs);
    signal?.addEventListener("abort", () => {
      clearTimeout(timer);
      const error = new Error("请求已取消");
      error.name = "AbortError";
      reject(error);
    }, { once: true });
  });
}

export async function withTransientRetry(operation, {
  delays = [400, 1200], signal, onRetry = () => {},
} = {}) {
  for (let attempt = 0; ; attempt += 1) {
    aborted(signal);
    try {
      return await operation();
    } catch (error) {
      if (error?.name === "AbortError" || !error?.transient || attempt >= delays.length) throw error;
      onRetry(attempt + 1, delays.length);
      await wait(delays[attempt], signal);
    }
  }
}

