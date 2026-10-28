export function CategoryIcon({ name }: { name: string }) {
  const common = {
    width: 14,
    height: 14,
    viewBox: "0 0 16 16",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.5,
    "aria-hidden": true as const,
  };
  if (name === "data") {
    return (
      <svg {...common}>
        <path d="M2.5 4.5v7c0 1.4 2.5 2.5 5.5 2.5s5.5-1.1 5.5-2.5v-7" />
        <path d="M2.5 4.5c0 1.4 2.5 2.5 5.5 2.5s5.5-1.1 5.5-2.5-2.5-2.5-5.5-2.5-5.5 1.1-5.5 2.5z" />
        <path d="M2.5 8c0 1.4 2.5 2.5 5.5 2.5s5.5-1.1 5.5-2.5" />
      </svg>
    );
  }
  if (name === "filter") {
    return (
      <svg {...common}>
        <path d="M2 2.5h12l-4.2 5.2V13l-3.6 1.5V7.7L2 2.5z" />
      </svg>
    );
  }
  if (name === "questions") {
    return (
      <svg {...common}>
        <circle cx="8" cy="8" r="5.5" />
        <path d="M6.3 6.4a1.8 1.8 0 1 1 2.5 1.7c-.6.3-.9.7-.9 1.4" />
        <path d="M8 11.4h.01" strokeLinecap="round" />
      </svg>
    );
  }
  if (name === "detect") {
    return (
      <svg {...common}>
        <circle cx="7" cy="7" r="4.2" />
        <path d="M10.2 10.2L14 14" />
      </svg>
    );
  }
  if (name === "combine") {
    return (
      <svg {...common}>
        <rect x="2" y="2" width="8" height="8" rx="1.2" />
        <rect x="6" y="6" width="8" height="8" rx="1.2" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <path d="M2.5 13.5V8M8 13.5V3M13.5 13.5V6" />
    </svg>
  );
}
