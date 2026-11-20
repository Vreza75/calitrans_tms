import type { LoadListItem } from "@/lib/api/types";
import { DispatchCard } from "@/components/dispatch/DispatchCard";

type Props = {
  stage: string;
  loads: LoadListItem[];
};

export function DispatchColumn({ stage, loads }: Props) {
  return (
    <section className="dispatch-column">
      <header className="dispatch-column__header">
        <h2>{stage}</h2>
        <span className="dispatch-column__count">{loads.length}</span>
      </header>
      {loads.length === 0 ? (
        <p className="dispatch-column__empty">No loads in this stage.</p>
      ) : (
        <ul className="dispatch-column__list">
          {loads.map((load) => (
            <DispatchCard key={load.id} load={load} />
          ))}
        </ul>
      )}
    </section>
  );
}
