// Phase 10B: useActiveDrivers/useAssignDriver/useTransitionLoad - mirrors
// realtime-hook-integration.test.tsx's render-through-QueryClientProvider
// pattern. apiClient itself is mocked (network boundary), not the hooks.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { loadKeys } from "@/lib/api/queryKeys";
import { useActiveDrivers, useAssignDriver, useTransitionLoad } from "@/lib/api/useDispatch";

const getMock = vi.fn();
const postMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  apiClient: {
    get: (...args: unknown[]) => getMock(...args),
    post: (...args: unknown[]) => postMock(...args),
  },
  ApiError: class ApiError extends Error {},
}));

function renderWithClient(ui: React.ReactElement, queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return { queryClient, ...render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>) };
}

function ActiveDriversProbe() {
  const { data } = useActiveDrivers();
  return <span data-testid="drivers">{data?.map((d) => d.driver_name).join(",")}</span>;
}

function AssignDriverProbe({ loadId }: { loadId: number }) {
  const assign = useAssignDriver(loadId);
  return (
    <button type="button" onClick={() => assign.mutate({ driver: "Joe Driver", truck: "T-100" })}>
      {assign.isSuccess ? "done" : "assign"}
    </button>
  );
}

function TransitionLoadProbe({ loadId }: { loadId: number }) {
  const transition = useTransitionLoad(loadId);
  return (
    <button
      type="button"
      onClick={() => transition.mutate({ new_status: "En Route to Pickup", note: "", override: false, override_reason: "" })}
    >
      {transition.isSuccess ? "done" : "transition"}
    </button>
  );
}

describe("useActiveDrivers", () => {
  afterEach(() => vi.clearAllMocks());

  it("fetches GET /api/v1/drivers/active", async () => {
    getMock.mockResolvedValue([{ driver_name: "Joe Driver", phone: "5551234567", truck_number: "T-100" }]);

    renderWithClient(<ActiveDriversProbe />);

    await waitFor(() => expect(screen.getByTestId("drivers")).toHaveTextContent("Joe Driver"));
    expect(getMock).toHaveBeenCalledWith("/api/v1/drivers/active", expect.anything());
  });
});

describe("useAssignDriver", () => {
  afterEach(() => vi.clearAllMocks());

  it("posts to /api/v1/loads/{id}/assign-driver and invalidates list/detail/timeline on success", async () => {
    postMock.mockResolvedValue({ ok: true, reason: "", status: "Ready to Dispatch", closeout_stage: "" });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    renderWithClient(<AssignDriverProbe loadId={42} />, queryClient);
    screen.getByRole("button").click();

    await waitFor(() => expect(screen.getByRole("button")).toHaveTextContent("done"));
    expect(postMock).toHaveBeenCalledWith("/api/v1/loads/42/assign-driver", { driver: "Joe Driver", truck: "T-100" });

    const invalidatedKeys = invalidateSpy.mock.calls.map((call) => call[0]?.queryKey);
    expect(invalidatedKeys).toContainEqual(loadKeys.lists());
    expect(invalidatedKeys).toContainEqual(loadKeys.detail(42));
    expect(invalidatedKeys).toContainEqual(loadKeys.timeline(42));
  });
});

describe("useTransitionLoad", () => {
  afterEach(() => vi.clearAllMocks());

  it("posts to /api/v1/loads/{id}/transition and invalidates list/detail/timeline on success", async () => {
    postMock.mockResolvedValue({ ok: true, reason: "", status: "En Route to Pickup", closeout_stage: "" });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    renderWithClient(<TransitionLoadProbe loadId={7} />, queryClient);
    screen.getByRole("button").click();

    await waitFor(() => expect(screen.getByRole("button")).toHaveTextContent("done"));
    expect(postMock).toHaveBeenCalledWith("/api/v1/loads/7/transition", {
      new_status: "En Route to Pickup",
      note: "",
      override: false,
      override_reason: "",
    });

    const invalidatedKeys = invalidateSpy.mock.calls.map((call) => call[0]?.queryKey);
    expect(invalidatedKeys).toContainEqual(loadKeys.lists());
    expect(invalidatedKeys).toContainEqual(loadKeys.detail(7));
    expect(invalidatedKeys).toContainEqual(loadKeys.timeline(7));
  });

  it("does not invalidate anything when the mutation fails", async () => {
    postMock.mockRejectedValue(new Error("Driver and truck must be assigned before starting En Route to Pickup."));
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    renderWithClient(<TransitionLoadProbe loadId={7} />, queryClient);
    screen.getByRole("button").click();

    await waitFor(() => expect(postMock).toHaveBeenCalled());
    expect(invalidateSpy).not.toHaveBeenCalled();
  });
});
