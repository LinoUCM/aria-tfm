import AuditLogsTable from "@/components/AuditLogsTable";

export default function AuditLogsPage() {
  return (
    <div className="p-8 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white tracking-wide">Audit Trail</h1>
        <p className="text-gray-400 text-sm mt-1">
          Immutable history of remediation runs and action auditing
        </p>
      </div>

      <AuditLogsTable />
    </div>
  );
}