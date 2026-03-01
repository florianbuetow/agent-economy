import { NavLink, Link } from "react-router-dom";

const NAV_ITEMS = [
  { to: "/observatory", label: "Macro Observatory" },
  { to: "/observatory/quarterly", label: "Quarterly Report" },
  { to: "/create-task", label: "Create Task" },
];

export default function TopNav() {
  return (
    <div className="flex items-center border-b border-border-strong bg-bg px-4 h-10 shrink-0">
      <Link to="/" className="font-mono text-[11px] font-bold tracking-[2px] uppercase text-text mr-8 pr-8 border-r border-border">
        ATE OBSERVATORY
      </Link>
      <div className="flex">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/observatory"}
            className={({ isActive }) =>
              `px-4 h-10 flex items-center font-mono text-[10px] uppercase tracking-[1px] border-b-2 ${
                isActive
                  ? "font-bold text-text border-text"
                  : "font-normal text-text-muted border-transparent"
              }`
            }
          >
            {item.label}
          </NavLink>
        ))}
      </div>
    </div>
  );
}
