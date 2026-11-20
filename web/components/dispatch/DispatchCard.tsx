"use client";

import Link from "next/link";
import { useState } from "react";

import { useAuth } from "@/lib/auth/AuthContext";
import type { LoadListItem } from "@/lib/api/types";
import { AssignDriverDialog } from "@/components/dispatch/AssignDriverDialog";
import { TransitionMenu } from "@/components/dispatch/TransitionMenu";

const DISPATCH_TRANSITION_PERMISSION = "dispatch:transition";

type Props = {
  load: LoadListItem;
};

// Cosmetic-only gate: hides mutation controls for roles without
// dispatch:transition (Accounting) - the real enforcement is server-side
// (api/auth.py's require_role(*MUTATE_OPERATIONS) plus application/loads/
// commands.py::transition_load's require_permission call). Hiding a
// button here is not the security boundary (see CLAUDE.md's "Two
// authorization layers" note in application/auth/permissions.py).
export function DispatchCard({ load }: Props) {
  const { hasPermission } = useAuth();
  const canMutate = hasPermission(DISPATCH_TRANSITION_PERMISSION);
  const [activeDialog, setActiveDialog] = useState<"assign" | "transition" | null>(null);

  return (
    <li className="dispatch-card">
      <div className="dispatch-card__header">
        <Link href={`/app/loads/${load.id}`}>{load.booking_number || `#${load.id}`}</Link>
        <span className="status-badge">{load.status}</span>
      </div>
      <div className="dispatch-card__body">
        <div>{load.customer}</div>
        <div>{[load.port, load.warehouse].filter(Boolean).join(" / ") || "-"}</div>
        <div>Driver: {load.driver_name || "Unassigned"}</div>
        <div>Truck: {load.truck_assigned || "-"}</div>
        <div>Need date: {load.delivery_need_date ?? "-"}</div>
      </div>

      {canMutate && activeDialog === null && (
        <div className="dispatch-card__actions">
          <button type="button" onClick={() => setActiveDialog("assign")}>
            Assign
          </button>
          <button type="button" onClick={() => setActiveDialog("transition")}>
            Transition
          </button>
        </div>
      )}

      {canMutate && activeDialog === "assign" && (
        <AssignDriverDialog load={load} onClose={() => setActiveDialog(null)} />
      )}

      {canMutate && activeDialog === "transition" && (
        <TransitionMenu load={load} onClose={() => setActiveDialog(null)} />
      )}
    </li>
  );
}
