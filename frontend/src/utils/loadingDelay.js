// Ensures minimum loading time so spinner is always visible
export const withMinimumDelay = async (promise, minDelayMs = 500) => {
  const startTime = Date.now();
  const result = await promise;
  const elapsedTime = Date.now() - startTime;
  
  if (elapsedTime < minDelayMs) {
    await new Promise(resolve => setTimeout(resolve, minDelayMs - elapsedTime));
  }
  
  return result;
};
