// Phase 10C: QuickActions - cosmetic permission gating (per-button) plus
// the real mutation wiring underneath. apiClient and useAuth are both
// mocked at their module boundary so this exercises QuickActions'
// own logic, not AuthProvider's login flow or the network.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { QuickActions } from "@/components/load-workspace/QuickActions";
import type { LoadDetail } from "@/lib/api/types";

const postMock = vi.fn();
let permissions: string[] = [];

vi.mock("@/lib/api/client", () => ({
  apiClient: { post: (...args: unknown[]) => postMock(...args) },
  ApiError: class ApiError extends Error {
    constructor(_status: number, message: string) {
      super(message);
    }
  },
}));

vi.mock("@/lib/auth/AuthContext", () => ({
  useAuth: () => ({ hasPermission: (permission: string) => permissions.includes(permission) }),
}));

function load(overrides: Partial<LoadDetail> = {}): LoadDetail {
  return {
    id: 1,
    type: "Import",
    load_id: "1",
    booking_number: "BKG-001",
    reference_number: "",
    container_number: "",
    customer: "Acme",
    port: "",
    warehouse: "",
    address: "",
    document_cutoff: null,
    delivery_need_date: null,
    load_date: null,
    lfd: null,
    status: "Ready to Dispatch",
    driver_name: "",
    truck_assigned: "",
    chassis: "",
    size: "",
    billing_notes: "",
    dispatcher_notes: "",
    invoice_status: "",
    driver_pay_status: "",
    closeout_stage: "",
    steamship_line: "",
    vessel_name: "",
    terminal: "",
    pickup_appointment: null,
    delivery_appointment: null,
    empty_return_location: "",
    empty_return_date: null,
    parent_booking_key: "",
    container_sequence: null,
    container_total: null,
    created_at: null,
    updated_at: null,
    ...overrides,
  };
}

function renderQuickActions(overrides: Partial<LoadDetail> = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <QuickActions load={load(overrides)} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
  permissions = [];
});

describe("QuickActions permission gating", () => {
  it("renders nothing when the actor has none of load:edit/load:verify/load:cancel", () => {
    permissions = [];
    const { container } = renderQuickActions();
    expect(container).toBeEmptyDOMElement();
  });

  it("shows only the buttons the actor's permissions grant", () => {
    permissions = ["load:verify"];
    renderQuickActions();

    expect(screen.queryByRole("button", { name: "Mark Missing Info" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Mark Booking Verified" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel Load" })).not.toBeInTheDocument();
  });

  it("hides Cancel Load when the load is already Cancelled, even with load:cancel", () => {
    permissions = ["load:cancel"];
    renderQuickActions({ status: "Cancelled" });

    expect(screen.queryByRole("button", { name: "Cancel Load" })).not.toBeInTheDocument();
  });
});

describe("QuickActions mutation wiring", () => {
  it("Mark Missing Info posts to /api/v1/loads/{id}/mark-missing-info with the note field", async () => {
    permissions = ["load:edit"];
    postMock.mockResolvedValue({ ok: true, load_id: 1, status: "Hold/Need Info" });
    renderQuickActions();

    fireEvent.change(screen.getByLabelText("Note (optional)"), { target: { value: "missing BOL" } });
    screen.getByRole("button", { name: "Mark Missing Info" }).click();

    await waitFor(() => expect(postMock).toHaveBeenCalled());
    expect(postMock).toHaveBeenCalledWith("/api/v1/loads/1/mark-missing-info", { note: "missing BOL" });
  });

  it("surfaces the server's error message on a rejected action", async () => {
    permissions = ["load:cancel"];
    postMock.mockRejectedValue(
      new (await import("@/lib/api/client")).ApiError(
        409,
        "Load is Completed; further operational status changes require an override.",
      ),
    );
    renderQuickActions();

    screen.getByRole("button", { name: "Cancel Load" }).click();

    await waitFor(() =>
      expect(screen.getByText("Load is Completed; further operational status changes require an override.")).toBeInTheDocument(),
    );
  });
});
