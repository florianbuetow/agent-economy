import { useState, useEffect, useCallback } from "react";
import type {
  AgentProfileResponse,
  AgentFeedResponse,
  AgentFeedEvent,
  AgentEarningsResponse,
} from "../types";
import {
  fetchAgentProfile,
  fetchAgentFeed,
  fetchAgentEarnings,
} from "../api/agents";

interface UseAgentProfileResult {
  profile: AgentProfileResponse | null;
  feed: AgentFeedEvent[];
  feedHasMore: boolean;
  earnings: AgentEarningsResponse | null;
  loading: boolean;
  feedLoading: boolean;
  roleFilter: string;
  typeFilter: string;
  timeFilter: string;
  setRoleFilter: (f: string) => void;
  setTypeFilter: (f: string) => void;
  setTimeFilter: (f: string) => void;
  loadMoreFeed: () => void;
}

export function useAgentProfile(agentId: string): UseAgentProfileResult {
  const [profile, setProfile] = useState<AgentProfileResponse | null>(null);
  const [feed, setFeed] = useState<AgentFeedEvent[]>([]);
  const [feedHasMore, setFeedHasMore] = useState(false);
  const [earnings, setEarnings] = useState<AgentEarningsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [feedLoading, setFeedLoading] = useState(false);
  const [roleFilter, setRoleFilter] = useState("ALL");
  const [typeFilter, setTypeFilter] = useState("ALL");
  const [timeFilter, setTimeFilter] = useState("ALL_TIME");

  // Load profile and earnings once
  useEffect(() => {
    setLoading(true);
    Promise.all([
      fetchAgentProfile(agentId),
      fetchAgentEarnings(agentId),
    ])
      .then(([profileData, earningsData]) => {
        setProfile(profileData);
        setEarnings(earningsData);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [agentId]);

  // Load feed when filters change
  useEffect(() => {
    setFeedLoading(true);
    const params: Record<string, string> = {};
    if (roleFilter !== "ALL") params.role = roleFilter;
    if (typeFilter !== "ALL") params.type = typeFilter;
    if (timeFilter !== "ALL_TIME") params.time = timeFilter;

    fetchAgentFeed(agentId, params)
      .then((data: AgentFeedResponse) => {
        setFeed(data.events);
        setFeedHasMore(data.has_more);
      })
      .catch(() => {})
      .finally(() => setFeedLoading(false));
  }, [agentId, roleFilter, typeFilter, timeFilter]);

  // Poll feed every 10s
  useEffect(() => {
    const interval = setInterval(() => {
      const params: Record<string, string> = {};
      if (roleFilter !== "ALL") params.role = roleFilter;
      if (typeFilter !== "ALL") params.type = typeFilter;
      if (timeFilter !== "ALL_TIME") params.time = timeFilter;

      fetchAgentFeed(agentId, params)
        .then((data: AgentFeedResponse) => {
          setFeed(data.events);
          setFeedHasMore(data.has_more);
        })
        .catch(() => {});
    }, 10000);
    return () => clearInterval(interval);
  }, [agentId, roleFilter, typeFilter, timeFilter]);

  const loadMoreFeed = useCallback(() => {
    if (feed.length === 0 || !feedHasMore) return;
    const lastId = feed[feed.length - 1].event_id;
    const params: Record<string, string | number> = { before: lastId };
    if (roleFilter !== "ALL") params.role = roleFilter;
    if (typeFilter !== "ALL") params.type = typeFilter;
    if (timeFilter !== "ALL_TIME") params.time = timeFilter;

    fetchAgentFeed(agentId, params)
      .then((data: AgentFeedResponse) => {
        setFeed((prev) => [...prev, ...data.events]);
        setFeedHasMore(data.has_more);
      })
      .catch(() => {});
  }, [agentId, feed, feedHasMore, roleFilter, typeFilter, timeFilter]);

  return {
    profile,
    feed,
    feedHasMore,
    earnings,
    loading,
    feedLoading,
    roleFilter,
    typeFilter,
    timeFilter,
    setRoleFilter,
    setTypeFilter,
    setTimeFilter,
    loadMoreFeed,
  };
}
