import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ConnectionIndicator } from "@/components/ConnectionIndicator";

describe("ConnectionIndicator", () => {
  it("never tells the user data is invalid when disconnected - only that live updates are unavailable", () => {
    render(<ConnectionIndicator state="disconnected" />);
    expect(screen.getByRole("status")).toHaveTextContent("Live updates temporarily unavailable");
  });

  it("shows the same safe message for an error state as for disconnected", () => {
    render(<ConnectionIndicator state="error" />);
    expect(screen.getByRole("status")).toHaveTextContent("Live updates temporarily unavailable");
  });

  it("shows a connected state distinctly", () => {
    render(<ConnectionIndicator state="connected" />);
    expect(screen.getByRole("status")).toHaveTextContent("Live updates connected");
  });
});
