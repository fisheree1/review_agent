import type { SVGProps } from "react";

export type IconName =
  | "book"
  | "chat"
  | "chevronLeft"
  | "chevronRight"
  | "close"
  | "document"
  | "error"
  | "help"
  | "library"
  | "menu"
  | "moon"
  | "panel"
  | "plus"
  | "refresh"
  | "sun"
  | "upload";

const paths: Record<IconName, React.ReactNode> = {
  book: <><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z" /></>,
  chat: <><path d="M20 11.5a8.5 8.5 0 0 1-8.5 8.5 9 9 0 0 1-3.6-.8L4 20l.8-3.9A8.5 8.5 0 1 1 20 11.5Z" /><path d="M8 11.5h8" /><path d="M8 14.5h5" /></>,
  chevronLeft: <path d="m15 18-6-6 6-6" />,
  chevronRight: <path d="m9 18 6-6-6-6" />,
  close: <><path d="m18 6-12 12" /><path d="m6 6 12 12" /></>,
  document: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" /><path d="M14 2v6h6" /><path d="M8 13h8" /><path d="M8 17h6" /></>,
  error: <><circle cx="12" cy="12" r="10" /><path d="M12 8v4" /><path d="M12 16h.01" /></>,
  help: <><circle cx="12" cy="12" r="10" /><path d="M9.1 9a3 3 0 1 1 5.83 1c0 2-3 2-3 4" /><path d="M12 18h.01" /></>,
  library: <><path d="m16 6 4 14" /><path d="M12 6v14" /><path d="M8 8v12" /><path d="M4 4v16" /></>,
  menu: <><path d="M4 6h16" /><path d="M4 12h16" /><path d="M4 18h16" /></>,
  moon: <path d="M20.7 13.3A8.5 8.5 0 1 1 10.7 3.3 7 7 0 0 0 20.7 13.3Z" />,
  panel: <><rect width="18" height="18" x="3" y="3" rx="2" /><path d="M15 3v18" /></>,
  plus: <><path d="M12 5v14" /><path d="M5 12h14" /></>,
  refresh: <><path d="M20 11a8 8 0 0 0-15-4l-2 3" /><path d="M3 4v6h6" /><path d="M4 13a8 8 0 0 0 15 4l2-3" /><path d="M21 20v-6h-6" /></>,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2" /><path d="M12 20v2" /><path d="m4.93 4.93 1.42 1.42" /><path d="m17.66 17.66 1.41 1.41" /><path d="M2 12h2" /><path d="M20 12h2" /><path d="m6.34 17.66-1.41 1.41" /><path d="m19.07 4.93-1.41 1.42" /></>,
  upload: <><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="m17 8-5-5-5 5" /><path d="M12 3v12" /></>,
};

interface IconProps extends SVGProps<SVGSVGElement> {
  name: IconName;
}

export function Icon({ name, ...props }: IconProps) {
  return (
    <svg
      aria-hidden="true"
      fill="none"
      height="20"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.8"
      viewBox="0 0 24 24"
      width="20"
      {...props}
    >
      {paths[name]}
    </svg>
  );
}
