import { useCallback, useRef, useState } from "react";

const PULL_THRESHOLD_PX = 72;
const MAX_PULL_PX = 96;

type Options = {
  onRefresh: () => Promise<void>;
  disabled?: boolean;
};

export function usePullToRefresh({ onRefresh, disabled = false }: Options): {
  pullOffset: number;
  refreshing: boolean;
  handlers: {
    onTouchStart: (e: React.TouchEvent) => void;
    onTouchMove: (e: React.TouchEvent) => void;
    onTouchEnd: () => void;
  };
} {
  const [pullOffset, setPullOffset] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const startY = useRef(0);
  const pulling = useRef(false);
  const busy = useRef(false);
  const pullOffsetRef = useRef(0);

  const runRefresh = useCallback(async () => {
    if (busy.current || disabled) return;
    busy.current = true;
    setRefreshing(true);
    setPullOffset(PULL_THRESHOLD_PX * 0.6);
    try {
      await onRefresh();
    } finally {
      setRefreshing(false);
      setPullOffset(0);
      busy.current = false;
    }
  }, [disabled, onRefresh]);

  const onTouchStart = useCallback(
    (e: React.TouchEvent) => {
      if (disabled || refreshing || busy.current) return;
      if (window.scrollY > 4) return;
      startY.current = e.touches[0]?.clientY ?? 0;
      pulling.current = true;
    },
    [disabled, refreshing],
  );

  const onTouchMove = useCallback(
    (e: React.TouchEvent) => {
      if (!pulling.current || disabled || refreshing) return;
      if (window.scrollY > 4) {
        pulling.current = false;
        setPullOffset(0);
        return;
      }
      const y = e.touches[0]?.clientY ?? startY.current;
      const delta = Math.max(0, y - startY.current);
      if (delta <= 0) {
        setPullOffset(0);
        return;
      }
      if (delta > 8) e.preventDefault();
      const next = Math.min(delta * 0.45, MAX_PULL_PX);
      pullOffsetRef.current = next;
      setPullOffset(next);
    },
    [disabled, refreshing],
  );

  const onTouchEnd = useCallback(() => {
    if (!pulling.current) return;
    pulling.current = false;
    const offset = pullOffsetRef.current;
    if (offset >= PULL_THRESHOLD_PX * 0.55) {
      void runRefresh();
    } else {
      pullOffsetRef.current = 0;
      setPullOffset(0);
    }
  }, [runRefresh]);

  return {
    pullOffset,
    refreshing,
    handlers: { onTouchStart, onTouchMove, onTouchEnd },
  };
}
