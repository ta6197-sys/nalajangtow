import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const workbook = Workbook.create();
const sheets = {
  contracts: ['contract_id', 'allowed_minutes', 'daily_demurrage_usd', 'notice_hours',
    'nor_valid', 'early_start_if_work', 'day_mode', 'weather_policy',
    'post_demurrage_exceptions', 'despatch_policy', 'holidays'],
  calls: ['call_id', 'contract_id', 'nor_at', 'available_at',
    'cargo_quantity_kg', 'work_minutes'],
};
for (const [name, headers] of Object.entries(sheets)) {
  const sheet = workbook.worksheets.add(name);
  const header = sheet.getRangeByIndexes(0, 0, 1, headers.length);
  header.values = [headers];
  header.format.font = { name: 'Aptos', size: 11, bold: true };
  header.format.rowHeight = 28;
  header.format.columnWidth = 26;
  sheet.freezePanes.freezeRows(1);
  sheet.showGridLines = false;
}
for (const name of Object.keys(sheets)) {
  const preview = await workbook.render({ sheetName: name, range: 'A1:K4', scale: 1.5 });
  await fs.writeFile(`/private/tmp/clx_template_${name}.png`,
    new Uint8Array(await preview.arrayBuffer()));
}
const outputDir = new URL('../outputs/clx_web_input/', import.meta.url);
await fs.mkdir(outputDir, { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(fileURLToPath(new URL('input_template.xlsx', outputDir)));
if (process.env.CLX_TEMPLATE_TEST === '1') {
  workbook.worksheets.getItem('contracts').getRange('A2:K2').values = [[
    'TEST-001', 1440, 6000, 0, 'Y', 'N', 'SHINC', 'COUNT', 'NONE', 'NONE', '',
  ]];
  workbook.worksheets.getItem('calls').getRange('A2:F3').values = [
    ['TEST-CALL-1', 'TEST-001', '2026-11-03 19:40', '2026-11-03 19:40', 1000000, 1200],
    ['TEST-CALL-2', 'TEST-001', '2026-11-03 20:00', '2026-11-03 20:00', 1000000, 1200],
  ];
  const sample = await SpreadsheetFile.exportXlsx(workbook);
  await sample.save('/private/tmp/clx_test_input.xlsx');
}
