import type { Catalog, PaletteItem, TemplateSummary } from "../types";
import { GROUPS } from "../editor/ui";
import { CategoryIcon } from "./CategoryIcon";
import { ExampleMenu } from "./ExampleMenu";

export function PaletteToolbar({
  catalog,
  templates,
  examplesOpen,
  onToggleExamples,
  onOpenExample,
  onPlace,
}: {
  catalog: Catalog;
  templates: TemplateSummary[];
  examplesOpen: boolean;
  onToggleExamples: () => void;
  onOpenExample: (id: string) => void;
  onPlace: (item: PaletteItem) => void;
}) {
  return (
    <div className="toolbar">
      {GROUPS.map((group) => (
        <div className="tool-group" key={group.id}>
          <span className={`tool-label cat-${group.id}`}>
            <CategoryIcon name={group.id} />
            {group.title}
          </span>
          <div className="tool-chips">
            {catalog.palette
              .filter((item) => group.categories.includes(item.category))
              .map((item) => (
                <button
                  key={`${item.node_type}-${item.label}`}
                  type="button"
                  className={`chip cat-${group.id}`}
                  title={item.blurb}
                  draggable
                  onDragStart={(event) => {
                    event.dataTransfer.setData("application/washdetect", JSON.stringify(item));
                    event.dataTransfer.effectAllowed = "move";
                  }}
                  onClick={() => onPlace(item)}
                >
                  {item.label}
                </button>
              ))}
          </div>
        </div>
      ))}
      <ExampleMenu templates={templates} open={examplesOpen} onToggle={onToggleExamples} onOpen={onOpenExample} />
    </div>
  );
}
