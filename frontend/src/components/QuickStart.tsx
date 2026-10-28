import { useState } from "react";
import { RECIPES } from "../editor/recipes";

export function QuickStart({ onOpen }: { onOpen: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="examples quick-start">
      <button type="button" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        What do you want to find?
      </button>
      {open && (
        <ul>
          {RECIPES.map((recipe) => (
            <li key={recipe.id}>
              <button
                type="button"
                onClick={() => {
                  setOpen(false);
                  onOpen(recipe.id);
                }}
              >
                <strong>{recipe.title}</strong>
                <span>{recipe.blurb}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
