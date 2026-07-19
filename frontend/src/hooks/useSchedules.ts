// useSchedules — 379 recurring-capture schedules (wired v3.66.382).
// FULL /api/ literals (scanner credit -> gui_parity spa_wired):
//   GET  /api/schedules
//   POST /api/schedules                  body {site_id, cadence_hours, label?, urls?}
//   POST /api/schedules/${id}/remove
//   POST /api/schedules/${id}/run_now
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiPost } from "@/lib/api-client";
import type {
  OkIdResult,
  ScheduleAddRequest,
  ScheduleRunResult,
  SchedulesResponse,
} from "@/lib/api-types";

export function useSchedules() {
  const qc = useQueryClient();
  const queryKey = ["schedules"];
  const invalidate = () => void qc.invalidateQueries({ queryKey });

  const query = useQuery<SchedulesResponse>({
    queryKey,
    queryFn: ({ signal }) => apiGet<SchedulesResponse>("/api/schedules", signal),
    refetchOnWindowFocus: false,
  });

  const add = useMutation<OkIdResult, Error, ScheduleAddRequest>({
    mutationFn: (req) => apiPost<OkIdResult>("/api/schedules", req),
    onSuccess: invalidate,
  });

  const remove = useMutation<{ ok: boolean }, Error, number>({
    mutationFn: (id) => apiPost<{ ok: boolean }>(`/api/schedules/${id}/remove`, {}),
    onSuccess: invalidate,
  });

  const runNow = useMutation<ScheduleRunResult, Error, number>({
    mutationFn: (id) => apiPost<ScheduleRunResult>(`/api/schedules/${id}/run_now`, {}),
    onSuccess: invalidate,
  });

  return { query, add, remove, runNow };
}
