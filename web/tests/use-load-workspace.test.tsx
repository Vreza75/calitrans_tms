// Phase 10C: useLoadDocuments/useLoadCommunications/useLoadTimeline +
// useUpdateLoadFields/useSaveLoadNote/useMarkMissingInfo/useVerifyBooking/
// useCancelLoad/useLogCommunication/useAttachDocument - mirrors
// use-dispatch.test.tsx's render-through-QueryClientProvider pattern.
// apiClient is mocked (network boundary), not the hooks.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { loadKeys } from "@/lib/api/queryKeys";
import {
  useAttachDocument,
  useCancelLoad,
  useLoadCommunications,
  useLoadDocuments,
  useLoadTimeline,
  useLogCommunication,
  useMarkMissingInfo,
  useSaveLoadNote,
  useUpdateLoadFields,
  useVerifyBooking,
} from "@/lib/api/useLoadWorkspace";

const getMock = vi.fn();
const postMock = vi.fn();
const putMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  apiClient: {
    get: (...args: unknown[]) => getMock(...args),
    post: (...args: unknown[]) => postMock(...args),
    put: (...args: unknown[]) => putMock(...args),
  },
  ApiError: class ApiError extends Error {},
}));

function renderWithClient(
  ui: React.ReactElement,
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
) {
  return { queryClient, ...render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>) };
}

afterEach(() => vi.clearAllMocks());

describe("query hooks", () => {
  it("useLoadDocuments fetches GET /api/v1/loads/{id}/documents", async () => {
    getMock.mockResolvedValue([{ id: 1, document_type: "load_pdf", filename: "rate.pdf", source: "web", status: "pending", created_at: null }]);

    function Probe() {
      const { data } = useLoadDocuments(1);
      return <span data-testid="docs">{data?.map((d) => d.filename).join(",")}</span>;
    }
    renderWithClient(<Probe />);

    await waitFor(() => expect(screen.getByTestId("docs")).toHaveTextContent("rate.pdf"));
    expect(getMock).toHaveBeenCalledWith("/api/v1/loads/1/documents", expect.anything());
  });

  it("useLoadCommunications fetches the paginated communications endpoint", async () => {
    getMock.mockResolvedValue({ items: [], page: 1, page_size: 50, total_items: 0, total_pages: 1, sort_by: "", sort_direction: "desc" });

    function Probe() {
      const { data } = useLoadCommunications(1);
      return <span data-testid="count">{data?.total_items}</span>;
    }
    renderWithClient(<Probe />);

    await waitFor(() => expect(screen.getByTestId("count")).toHaveTextContent("0"));
    expect(getMock).toHaveBeenCalledWith("/api/v1/loads/1/communications?page=1&limit=50", expect.anything());
  });

  it("useLoadTimeline fetches the paginated timeline endpoint", async () => {
    getMock.mockResolvedValue({ items: [], page: 1, page_size: 50, total_items: 0, total_pages: 1 });

    function Probe() {
      const { data } = useLoadTimeline(1);
      return <span data-testid="count">{data?.total_items}</span>;
    }
    renderWithClient(<Probe />);

    await waitFor(() => expect(screen.getByTestId("count")).toHaveTextContent("0"));
    expect(getMock).toHaveBeenCalledWith("/api/v1/loads/1/timeline?page=1&limit=50", expect.anything());
  });
});

describe("useUpdateLoadFields", () => {
  it("PUTs to /api/v1/loads/{id} and invalidates lists/detail/timeline", async () => {
    putMock.mockResolvedValue({ ok: true, load_id: 1, status: "Active", reason: "" });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    function Probe() {
      const update = useUpdateLoadFields(1);
      return (
        <button type="button" onClick={() => update.mutate({ updates: { customer: "Acme" } })}>
          {update.isSuccess ? "done" : "save"}
        </button>
      );
    }
    renderWithClient(<Probe />, queryClient);
    screen.getByRole("button").click();

    await waitFor(() => expect(screen.getByRole("button")).toHaveTextContent("done"));
    expect(putMock).toHaveBeenCalledWith("/api/v1/loads/1", { updates: { customer: "Acme" } });
    const invalidatedKeys = invalidateSpy.mock.calls.map((call) => call[0]?.queryKey);
    expect(invalidatedKeys).toContainEqual(loadKeys.lists());
    expect(invalidatedKeys).toContainEqual(loadKeys.detail(1));
    expect(invalidatedKeys).not.toContainEqual(loadKeys.documents(1)); // fields-scope invalidation only
  });
});

describe("useSaveLoadNote", () => {
  it("posts to /api/v1/loads/{id}/notes", async () => {
    postMock.mockResolvedValue({ ok: true, load_id: 1 });

    function Probe() {
      const save = useSaveLoadNote(1);
      return (
        <button type="button" onClick={() => save.mutate({ note: "called customer" })}>
          {save.isSuccess ? "done" : "save"}
        </button>
      );
    }
    renderWithClient(<Probe />);
    screen.getByRole("button").click();

    await waitFor(() => expect(screen.getByRole("button")).toHaveTextContent("done"));
    expect(postMock).toHaveBeenCalledWith("/api/v1/loads/1/notes", { note: "called customer" });
  });
});

describe("status quick-action hooks", () => {
  it.each([
    ["useMarkMissingInfo", useMarkMissingInfo, "mark-missing-info"],
    ["useVerifyBooking", useVerifyBooking, "verify-booking"],
    ["useCancelLoad", useCancelLoad, "cancel"],
  ] as const)("%s posts to /api/v1/loads/{id}/%s", async (_name, hook, path) => {
    postMock.mockResolvedValue({ ok: true, load_id: 1, status: "Updated" });

    function Probe() {
      const action = hook(1);
      return (
        <button type="button" onClick={() => action.mutate({ note: "x" })}>
          {action.isSuccess ? "done" : "go"}
        </button>
      );
    }
    renderWithClient(<Probe />);
    screen.getByRole("button").click();

    await waitFor(() => expect(screen.getByRole("button")).toHaveTextContent("done"));
    expect(postMock).toHaveBeenCalledWith(`/api/v1/loads/1/${path}`, { note: "x" });
  });
});

describe("useLogCommunication", () => {
  it("posts to /api/v1/loads/{id}/communications and invalidates the communications key", async () => {
    postMock.mockResolvedValue({ ok: true });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    function Probe() {
      const log = useLogCommunication(1);
      return (
        <button
          type="button"
          onClick={() => log.mutate({ message_type: "note", direction: "internal", recipient: "", message_body: "hi" })}
        >
          {log.isSuccess ? "done" : "log"}
        </button>
      );
    }
    renderWithClient(<Probe />, queryClient);
    screen.getByRole("button").click();

    await waitFor(() => expect(screen.getByRole("button")).toHaveTextContent("done"));
    expect(postMock).toHaveBeenCalledWith("/api/v1/loads/1/communications", {
      message_type: "note",
      direction: "internal",
      recipient: "",
      message_body: "hi",
    });
    const invalidatedKeys = invalidateSpy.mock.calls.map((call) => call[0]?.queryKey);
    expect(invalidatedKeys).toContainEqual(loadKeys.communications(1));
  });
});

describe("useAttachDocument", () => {
  it("posts a FormData body to /api/v1/loads/{id}/documents and invalidates the documents key", async () => {
    postMock.mockResolvedValue({ ok: true, document_id: 1, status: "pending" });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const file = new File(["pdf bytes"], "rate.pdf", { type: "application/pdf" });

    function Probe() {
      const attach = useAttachDocument(1);
      return (
        <button type="button" onClick={() => attach.mutate(file)}>
          {attach.isSuccess ? "done" : "upload"}
        </button>
      );
    }
    renderWithClient(<Probe />, queryClient);
    screen.getByRole("button").click();

    await waitFor(() => expect(screen.getByRole("button")).toHaveTextContent("done"));
    const [url, body] = postMock.mock.calls[0];
    expect(url).toBe("/api/v1/loads/1/documents");
    expect(body).toBeInstanceOf(FormData);
    expect((body as FormData).get("file")).toBe(file);
    const invalidatedKeys = invalidateSpy.mock.calls.map((call) => call[0]?.queryKey);
    expect(invalidatedKeys).toContainEqual(loadKeys.documents(1));
  });
});
