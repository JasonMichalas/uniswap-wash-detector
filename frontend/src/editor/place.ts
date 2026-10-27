const BOX_W = 280;
const BOX_H = 176;
const GAP_X = 32;
const GAP_Y = 24;

type Placed = {
  position: { x: number; y: number };
  width?: number | null;
  height?: number | null;
};

export function freePosition(nodes: Placed[], preferred: { x: number; y: number }): { x: number; y: number } {
  const origin = { x: Math.max(24, preferred.x), y: Math.max(24, preferred.y) };
  for (let row = 0; row < 12; row++) {
    for (let col = 0; col < 6; col++) {
      const next = {
        x: origin.x + col * (BOX_W + GAP_X),
        y: origin.y + row * (BOX_H + GAP_Y),
      };
      if (!overlaps(next, nodes)) return next;
    }
  }
  return { x: origin.x, y: origin.y + nodes.length * (BOX_H + GAP_Y) };
}

function overlaps(spot: { x: number; y: number }, nodes: Placed[]): boolean {
  return nodes.some((node) => {
    const width = node.width && node.width > 40 ? node.width : BOX_W;
    const height = node.height && node.height > 40 ? node.height : BOX_H;
    return (
      spot.x < node.position.x + width + 12 &&
      spot.x + BOX_W + 12 > node.position.x &&
      spot.y < node.position.y + height + 12 &&
      spot.y + BOX_H + 12 > node.position.y
    );
  });
}
