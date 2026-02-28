import { useEffect, useRef, useState, useCallback } from "react";
import type { EventItem } from "../types";

const MAX_EVENTS = 500;

export function useEventStream() {
  const [events, setEvents] = useState<EventItem[]>([]);
  const [connected, setConnected] = useState(false);
  const [paused, setPaused] = useState(false);
  const pausedRef = useRef(false);

  const togglePause = useCallback(() => {
    setPaused((p) => {
      pausedRef.current = !p;
      return !p;
    });
  }, []);

  useEffect(() => {
    const es = new EventSource("/api/events/stream");

    es.addEventListener("economy_event", (e: MessageEvent) => {
      if (pausedRef.current) return;
      try {
        const event: EventItem = JSON.parse(e.data as string);
        setEvents((prev) => [event, ...prev].slice(0, MAX_EVENTS));
      } catch {
        // skip malformed events
      }
    });

    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);

    return () => es.close();
  }, []);

  return { events, connected, paused, togglePause };
}
