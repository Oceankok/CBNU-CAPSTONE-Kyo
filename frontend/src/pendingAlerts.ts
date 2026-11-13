// Decides which unreviewed events are really new between two polls of the summary API.
// The summary only returns the newest N unreviewed events (a sample). When one of them is
// reviewed, an OLDER event slides into the sample; that must not be announced as new (#240).
// Pure on purpose so it can be tested without a browser.
export interface Sampled {
  event_id: string;
  timestamp_start: string;
}

export interface Sample {
  ids: Set<string>;
  // Capture time of the oldest event in the sample, when the sample was full (more may be hidden)
  oldestWhenFull: number | null;
}

const time = (e: Sampled) => Date.parse(e.timestamp_start);

// Remember only the latest sample: nothing accumulates over a long session
export function snapshot(list: Sampled[], limit: number): Sample {
  const times = list.map(time).filter(Number.isFinite);
  return {
    ids: new Set(list.map((e) => e.event_id)),
    oldestWhenFull:
      list.length >= limit && times.length ? Math.min(...times) : null,
  };
}

export function countNew(prev: Sample, list: Sampled[]): number {
  return list.filter((e) => {
    if (prev.ids.has(e.event_id)) return false;
    // The earlier sample showed every unreviewed event, so an unknown id is a new one
    if (prev.oldestWhenFull === null) return true;
    // Otherwise only events captured after the old window's tail can be new;
    // anything at or before it was already waiting outside the window
    const t = time(e);
    return Number.isFinite(t) && t > prev.oldestWhenFull;
  }).length;
}
