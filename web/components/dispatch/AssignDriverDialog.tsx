"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import { useActiveDrivers, useAssignDriver } from "@/lib/api/useDispatch";
import type { LoadListItem } from "@/lib/api/types";

type Props = {
  load: LoadListItem;
  onClose: () => void;
};

// Phase 10B: assignment without a status change - POST /loads/{id}/assign-driver
// re-applies the load's current status server-side (see
// api/routers/loads.py::assign_driver_endpoint's own docstring), so this
// dialog never sends new_status itself.
export function AssignDriverDialog({ load, onClose }: Props) {
  const { data: drivers, isLoading: isLoadingDrivers, isError: isDriversError } = useActiveDrivers();
  const assignDriver = useAssignDriver(load.id);
  const [driverName, setDriverName] = useState(load.driver_name || "");
  const [truck, setTruck] = useState(load.truck_assigned || "");

  function handleDriverSelect(name: string) {
    setDriverName(name);
    const match = drivers?.find((driver) => driver.driver_name === name);
    if (match) setTruck(match.truck_number);
  }

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!driverName.trim()) return;
    assignDriver.mutate(
      { driver: driverName.trim(), truck: truck.trim() || null },
      { onSuccess: onClose },
    );
  }

  return (
    <form className="dispatch-dialog" onSubmit={handleSubmit} aria-label={`Assign driver for load ${load.booking_number}`}>
      <div className="field">
        <label htmlFor={`driver-${load.id}`}>Driver</label>
        {isLoadingDrivers && <p role="status">Loading drivers...</p>}
        {isDriversError && <p role="alert" className="field-error">Unable to load the active driver roster.</p>}
        {drivers && (
          <select id={`driver-${load.id}`} value={driverName} onChange={(event) => handleDriverSelect(event.target.value)}>
            <option value="">Select a driver...</option>
            {drivers.map((driver) => (
              <option key={driver.driver_name} value={driver.driver_name}>
                {driver.driver_name} ({driver.truck_number || "no truck on file"})
              </option>
            ))}
          </select>
        )}
      </div>

      <div className="field">
        <label htmlFor={`truck-${load.id}`}>Truck</label>
        <input id={`truck-${load.id}`} value={truck} onChange={(event) => setTruck(event.target.value)} />
      </div>

      {assignDriver.isError && (
        <p role="alert" className="field-error">
          {assignDriver.error instanceof ApiError ? assignDriver.error.message : "Unable to assign this driver."}
        </p>
      )}

      <div className="dispatch-dialog__actions">
        <button type="button" onClick={onClose} disabled={assignDriver.isPending}>
          Cancel
        </button>
        <button type="submit" disabled={!driverName.trim() || assignDriver.isPending}>
          {assignDriver.isPending ? "Assigning..." : "Assign"}
        </button>
      </div>
    </form>
  );
}
