import type { BundleContent } from './educationBundles';

export function bundleSelectionRequest(recommendationIds: string[]) {
  return { recommendation_ids: [...recommendationIds] };
}

export function bundleCreateRequest(
  recommendationIds: string[],
  expectedSnapshot: string,
  content: BundleContent,
) {
  return {
    ...bundleSelectionRequest(recommendationIds),
    expected_snapshot: expectedSnapshot,
    ...content,
  };
}

export function bundleGenerateRequest(
  recommendationIds: string[],
  expectedSnapshot: string,
) {
  return {
    ...bundleSelectionRequest(recommendationIds),
    expected_snapshot: expectedSnapshot,
  };
}

export function bundleVersionRequest(expectedVersion: number) {
  return { expected_version: expectedVersion };
}

export function bundlePublishRequest(expectedVersion: number) {
  return { expected_version: expectedVersion, approved: true };
}
