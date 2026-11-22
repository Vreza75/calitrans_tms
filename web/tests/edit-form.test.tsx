// P1 regression (Codex review on PR #26): EditForm/NotesEditor must
// never submit an untouched field's value, even when the server's data
// refreshes between mount and submit, and must preserve the user's
// unsaved edits on a 409 conflict rather than discarding them.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EditForm } from "@/components/load-workspace/EditForm";
import { NotesEditor } from "@/components/load-workspace/NotesEditor";
import type { LoadDetail } from "@/lib/api/types";

const putMock = vi.fn();
const postMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  apiClient: {
    put: (...args: unknown[]) => putMock(...args),
    post: (...args: unknown[]) => postMock(...args),
  },
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

vi.mock("@/lib/auth/AuthContext", () => ({
  useAuth: () => ({ hasPermission: () => true }),
}));

function load(overrides: Partial<LoadDetail> = {}): LoadDetail {
  return {
    id: 1,
    type: "Import",
    load_id: "1",
    booking_number: "BKG-001",
    reference_number: "",
    container_number: "",
    customer: "Original Customer",
    port: "Original Port",
    warehouse: "Original Warehouse",
    address: "Original Address",
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
    dispatcher_notes: "original note",
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
    updated_at: "2026-08-15T12:00:00Z",
    ...overrides,
  };
}

function renderWithClient(ui: React.ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

afterEach(() => vi.clearAllMocks());

describe("EditForm - dirty-field tracking survives a server refresh", () => {
  it("only submits the field the user actually touched, not a field that merely changed server-side", () => {
    putMock.mockResolvedValue({ ok: true, load_id: 1, status: "Ready to Dispatch" });
    const { rerender } = renderWithClient(<EditForm load={load()} />);

    fireEvent.change(screen.getByLabelText("Customer"), { target: { value: "Edited Customer" } });

    // Someone else's concurrent write refreshes the Port field server-side
    // (e.g. via realtime invalidation) - the user never touched Port.
    const refreshed = load({ port: "Someone Else's New Port" });
    rerender(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <EditForm load={refreshed} />
      </QueryClientProvider>,
    );

    // Untouched field displays the fresh server value, not a stale snapshot.
    expect(screen.getByLabelText("Port / Pickup")).toHaveValue("Someone Else's New Port");
    // Touched field keeps the user's own edit.
    expect(screen.getByLabelText("Customer")).toHaveValue("Edited Customer");
  });

  it("submits only the dirty field's key, excluding every untouched field", async () => {
    putMock.mockResolvedValue({ ok: true, load_id: 1, status: "Ready to Dispatch" });
    renderWithClient(<EditForm load={load()} />);

    fireEvent.change(screen.getByLabelText("Customer"), { target: { value: "Edited Customer" } });
    screen.getByRole("button", { name: "Save Changes" }).click();

    await waitFor(() => expect(putMock).toHaveBeenCalled());
    const [, body] = putMock.mock.calls[0];
    expect(body.updates).toEqual({ customer: "Edited Customer" });
    expect(body.expected_updated_at).toBe("2026-08-15T12:00:00Z");
  });

  it("preserves the unsaved edit and shows a conflict message on a 409", async () => {
    const { ApiError } = await import("@/lib/api/client");
    putMock.mockRejectedValue(new ApiError(409, "Load was updated by someone else since you loaded it."));
    renderWithClient(<EditForm load={load()} />);

    fireEvent.change(screen.getByLabelText("Customer"), { target: { value: "My Unsaved Edit" } });
    screen.getByRole("button", { name: "Save Changes" }).click();

    await waitFor(() =>
      expect(screen.getByText(/changed by someone else since you opened it/)).toBeInTheDocument(),
    );
    expect(screen.getByLabelText("Customer")).toHaveValue("My Unsaved Edit");
  });

  it("the Save Changes button stays disabled until a field is actually touched", () => {
    renderWithClient(<EditForm load={load()} />);
    expect(screen.getByRole("button", { name: "Save Changes" })).toBeDisabled();
  });
});

describe("NotesEditor - same dirty-tracking/conflict behavior for the single notes field", () => {
  it("displays the fresh server note when untouched, after a refresh", () => {
    const { rerender } = renderWithClient(<NotesEditor load={load()} />);
    expect(screen.getByLabelText("Dispatcher Notes")).toHaveValue("original note");

    rerender(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <NotesEditor load={load({ dispatcher_notes: "someone else's new note" })} />
      </QueryClientProvider>,
    );

    expect(screen.getByLabelText("Dispatcher Notes")).toHaveValue("someone else's new note");
  });

  it("returns 409 for a stale note write and preserves the unsaved text", async () => {
    const { ApiError } = await import("@/lib/api/client");
    postMock.mockRejectedValue(new ApiError(409, "Load was updated by someone else since you loaded it."));
    renderWithClient(<NotesEditor load={load()} />);

    fireEvent.change(screen.getByLabelText("Dispatcher Notes"), { target: { value: "my unsaved note" } });
    screen.getByRole("button", { name: "Save Notes" }).click();

    await waitFor(() =>
      expect(screen.getByText(/changed by someone else since you opened it/)).toBeInTheDocument(),
    );
    expect(screen.getByLabelText("Dispatcher Notes")).toHaveValue("my unsaved note");
  });

  it("sends expected_updated_at with the note save", async () => {
    postMock.mockResolvedValue({ ok: true, load_id: 1 });
    renderWithClient(<NotesEditor load={load()} />);

    fireEvent.change(screen.getByLabelText("Dispatcher Notes"), { target: { value: "called customer" } });
    screen.getByRole("button", { name: "Save Notes" }).click();

    await waitFor(() => expect(postMock).toHaveBeenCalled());
    expect(postMock).toHaveBeenCalledWith("/api/v1/loads/1/notes", {
      note: "called customer",
      expected_updated_at: "2026-08-15T12:00:00Z",
    });
  });
});
