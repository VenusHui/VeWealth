import { Table } from 'antd'
import type { TableProps } from 'antd'

type Props<T extends object> = TableProps<T>

export function BacktestTable<T extends object>(props: Props<T>) {
  return (
    <Table<T>
      size="small"
      bordered
      {...props}
    />
  )
}
