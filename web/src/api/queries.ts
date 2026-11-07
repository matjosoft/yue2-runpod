// TanStack Query hooks over the local server API. Server state lives here, nowhere else.
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import {
  ApiError,
  api,
  call,
  isTerminal,
  type Generation,
  type GenerationCreate,
  type SettingsUpdate,
  type WorkerCheck,
} from "./client";

/** PRD section 9: the UI polls the server every 1.5 s while a generation is not terminal. */
export const GENERATION_POLL_MS = 1500;
/** PRD section 9: the status pill polls /api/health every 10 s while the tab is visible. */
export const HEALTH_POLL_MS = 10_000;

export const queryKeys = {
  health: ["health"] as const,
  capabilities: ["capabilities"] as const,
  settings: ["settings"] as const,
  generations: ["generations"] as const,
  generation: (id: string) => ["generation", id] as const,
};

export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: () => call(api.GET("/api/health")),
    // Polling pauses while the tab is hidden (refetchIntervalInBackground is false).
    refetchInterval: HEALTH_POLL_MS,
  });
}

export function useCapabilities() {
  return useQuery({
    queryKey: queryKeys.capabilities,
    queryFn: () => call(api.GET("/api/capabilities")),
    staleTime: 60_000,
  });
}

export function useGeneration(id: string | null | undefined) {
  return useQuery({
    queryKey: queryKeys.generation(id ?? ""),
    queryFn: () => call(api.GET("/api/generations/{id}", { params: { path: { id: id ?? "" } } })),
    enabled: Boolean(id),
    refetchInterval: (query) => {
      const data = query.state.data;
      if (data && isTerminal(data.status)) return false;
      const error = query.state.error;
      if (error instanceof ApiError && error.status === 404) return false;
      return GENERATION_POLL_MS;
    },
  });
}

export function useGenerationList() {
  return useInfiniteQuery({
    queryKey: queryKeys.generations,
    queryFn: ({ pageParam }) =>
      call(
        api.GET("/api/generations", {
          params: { query: pageParam === null ? {} : { cursor: pageParam } },
        }),
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    // Keep status chips current while anything in the list is still in progress.
    refetchInterval: (query) => {
      const items = query.state.data?.pages.flatMap((p) => p.items) ?? [];
      return items.some((g) => !isTerminal(g.status)) ? GENERATION_POLL_MS : false;
    },
  });
}

export function useCreateGeneration() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: GenerationCreate) => call(api.POST("/api/generations", { body })),
    onSuccess: (generation: Generation) => {
      client.setQueryData(queryKeys.generation(generation.id), generation);
      void client.invalidateQueries({ queryKey: queryKeys.generations });
    },
  });
}

export function useDeleteGeneration() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      call(api.DELETE("/api/generations/{id}", { params: { path: { id } } })),
    onSuccess: (_data, id) => {
      client.removeQueries({ queryKey: queryKeys.generation(id) });
      void client.invalidateQueries({ queryKey: queryKeys.generations });
    },
  });
}

export function useSettings() {
  return useQuery({
    queryKey: queryKeys.settings,
    queryFn: () => call(api.GET("/api/settings")),
  });
}

export function useSaveSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: SettingsUpdate) => call(api.PUT("/api/settings", { body })),
    onSuccess: (settings) => {
      client.setQueryData(queryKeys.settings, settings);
      void client.invalidateQueries({ queryKey: queryKeys.capabilities });
    },
  });
}

/** Check the worker in the background and show the result in the status pill. Used
 * after Save: the server's cached check still describes the old settings (U-5). */
export function recheckWorker(client: QueryClient): void {
  call(api.POST("/api/worker/check")).then(
    (check) => {
      client.setQueryData(queryKeys.health, { ok: true as const, worker: check });
    },
    () => {
      void client.invalidateQueries({ queryKey: queryKeys.health });
    },
  );
}

export function useWorkerCheck() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => call(api.POST("/api/worker/check")),
    onSuccess: (check: WorkerCheck) => {
      // Show the fresh result in the status pill at once (U-5).
      client.setQueryData(queryKeys.health, { ok: true as const, worker: check });
      void client.invalidateQueries({ queryKey: queryKeys.capabilities });
    },
  });
}
