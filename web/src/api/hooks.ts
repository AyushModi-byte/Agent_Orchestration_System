import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "./client";
import type { DecisionBody, TaskEventMsg } from "./types";

const TERMINAL = new Set(["completed", "failed", "needs_human", "taken_over"]);

export const isTerminal = (status: string | undefined): boolean => !!status && TERMINAL.has(status);

export function useTasks() {
  return useQuery({
    queryKey: ["tasks"],
    queryFn: async () => unwrap(await api.GET("/tasks", { params: { query: { limit: 30 } } })),
    refetchInterval: 4000,
  });
}

export function useTask(id: string | undefined) {
  return useQuery({
    queryKey: ["task", id],
    enabled: !!id,
    queryFn: async () => unwrap(await api.GET("/tasks/{task_id}", { params: { path: { task_id: id! } } })),
    // poll while the run is live; stop once it settles (a paused run is polled slowly)
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      return isTerminal(s) ? false : s === "awaiting_approval" ? 3000 : 1000;
    },
  });
}

export function useCreateTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: {
      request: string;
      repo: string;
      pr_number: number;
      head_ref: string;
      user_id: string;
      review_required: boolean;
    }) => unwrap(await api.POST("/tasks", { body: { ...body, labels: [] } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tasks"] }),
  });
}

/** Server-sent events for one task. Reconnects are handled by the browser; we only collect. */
export function useTaskEvents(taskId: string | undefined) {
  const [events, setEvents] = useState<TaskEventMsg[]>([]);
  const [ended, setEnded] = useState(false);
  const qc = useQueryClient();
  const seen = useRef(0);

  useEffect(() => {
    setEvents([]);
    setEnded(false);
    seen.current = 0;
    if (!taskId) return;
    const base = import.meta.env.VITE_API_BASE ?? "/api";
    const es = new EventSource(`${base}/tasks/${taskId}/events`);
    const types = [
      "intake", "plan", "plan_failed", "subtask_done", "subtask_failed", "review", "synthesized",
      "delivered", "decision", "notified", "modify_invalid", "action_done", "action_failed",
      "approval_requested", "memory_saved", "memory_failed", "failed",
    ];
    const onMsg = (e: MessageEvent<string>) => {
      try {
        const data = JSON.parse(e.data) as Record<string, unknown>;
        setEvents((prev) => [...prev, { ...data, type: e.type }]);
      } catch {
        /* ignore a malformed frame */
      }
      qc.invalidateQueries({ queryKey: ["task", taskId] });
      if (e.type === "approval_requested") qc.invalidateQueries({ queryKey: ["approvals"] });
    };
    types.forEach((t) => es.addEventListener(t, onMsg as EventListener));
    es.addEventListener("end", () => {
      setEnded(true);
      es.close();
      qc.invalidateQueries({ queryKey: ["task", taskId] });
    });
    return () => es.close();
  }, [taskId, qc]);

  return { events, ended };
}

export function useApprovals(status: string | undefined) {
  return useQuery({
    queryKey: ["approvals", status ?? "all"],
    queryFn: async () => unwrap(await api.GET("/approvals", { params: { query: status ? { status } : {} } })),
    refetchInterval: 3000,
  });
}

export function useApproval(id: string | undefined) {
  return useQuery({
    queryKey: ["approval", id],
    enabled: !!id,
    queryFn: async () => unwrap(await api.GET("/approvals/{approval_id}", { params: { path: { approval_id: id! } } })),
    refetchInterval: 3000,
  });
}

export function useDecide(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: DecisionBody) =>
      unwrap(await api.POST("/approvals/{approval_id}/decision", { params: { path: { approval_id: id } }, body: { ...body } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["approvals"] });
      qc.invalidateQueries({ queryKey: ["approval", id] });
      qc.invalidateQueries({ queryKey: ["task"] });
    },
  });
}

export function useChat(id: string) {
  return useMutation({
    mutationFn: async (question: string) =>
      unwrap(await api.POST("/approvals/{approval_id}/chat", { params: { path: { approval_id: id } }, body: { question } })),
  });
}

export function useTrace(taskId: string | undefined) {
  return useQuery({
    queryKey: ["trace", taskId],
    enabled: !!taskId,
    retry: false,
    queryFn: async () => unwrap(await api.GET("/traces/{task_id}", { params: { path: { task_id: taskId! } } })),
  });
}

export function useSpanDetail(taskId: string, spanId: string | null) {
  return useQuery({
    queryKey: ["span", taskId, spanId],
    enabled: !!spanId,
    queryFn: async () =>
      unwrap(await api.GET("/traces/{task_id}/spans/{span_id}", { params: { path: { task_id: taskId, span_id: spanId! } } })),
  });
}

export function useMemory(userId: string) {
  return useQuery({
    queryKey: ["memory", userId],
    queryFn: async () => unwrap(await api.GET("/memory", { params: { query: { user_id: userId } } })),
  });
}

export function useDeleteMemory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (userId: string) =>
      unwrap(await api.DELETE("/memory/{user_id}", { params: { path: { user_id: userId } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["memory"] }),
  });
}

export function useCost(days: number) {
  return useQuery({
    queryKey: ["cost", days],
    queryFn: async () => unwrap(await api.GET("/metrics/cost", { params: { query: { days } } })),
    refetchInterval: 10000,
  });
}
