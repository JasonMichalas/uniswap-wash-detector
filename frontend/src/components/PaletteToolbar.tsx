import type { Catalog, PaletteItem, TemplateSummary } from "../types";
import { boxTitle, whatItDoes } from "../editor/boxCopy";
import { GROUPS } from "../editor/ui";
import { CategoryIcon } from "./CategoryIcon";
import { ExampleMenu } from "./ExampleMenu";
import { QuickStart } from "./QuickStart";

export function PaletteToolbar({
  catalog,
  templates,
  examplesOpen,
  onToggleExamples,
  onOpenExample,
  onOpenRecipe,
  onPlace,
}: {
  catalog: Catalog;
  templates: TemplateSummary[];
  examplesOpen: boolean;
  onToggleExamples: () => void;
  onOpenExample: (id: string) => void;
  onOpenRecipe: (id: string) => void;
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
                  draggable
                  onDragStart={(event) => {
                    event.dataTransfer.setData("application/washdetect", JSON.stringify(item));
                    event.dataTransfer.effectAllowed = "move";
                  }}
                  onClick={() => onPlace(item)}
                >
                  {boxTitle(item.node_type, item.data)}
                  <span className="chip-tip" role="tooltip">
                    {whatItDoes(item.node_type, item.data)}
                  </span>
                </button>
              ))}
          </div>
        </div>
      ))}
      <QuickStart onOpen={onOpenRecipe} />
      <ExampleMenu templates={templates} open={examplesOpen} onToggle={onToggleExamples} onOpen={onOpenExample} />
    </div>
  );
}
