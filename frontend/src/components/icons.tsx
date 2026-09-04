// Material Symbols (outlined, 24px) — official paths from google/material-design-icons
// viewBox 0 -960 960 960. MIT-2.0 / CC-BY-4.0 dual license applies.
const V = '0 -960 960 960'

const P_FormatBold = ['M272-200v-560h221q65 0 120 40t55 111q0 51-23 78.5T602-491q25 11 55.5 41t30.5 90q0 89-65 124.5T501-200H272Zm121-112h104q48 0 58.5-24.5T566-372q0-11-10.5-35.5T494-432H393v120Zm0-228h93q33 0 48-17t15-38q0-24-17-39t-44-15h-95v109Z']
const P_FormatItalic = ['M200-200v-100h160l120-360H320v-100h400v100H580L460-300h140v100H200Z']
const P_FormatUnderlined = ['M200-120v-80h560v80H200Zm280-160q-101 0-157-63t-56-167v-330h103v336q0 56 28 91t82 35q54 0 82-35t28-91v-336h103v330q0 104-56 167t-157 63Z']
const P_InkHighlighter = ['M544-400 440-504 240-304l104 104 200-200Zm-47-161 104 104 199-199-104-104-199 199Zm-84-28 216 216-229 229q-24 24-56 24t-56-24l-2-2-26 26H60l126-126-2-2q-24-24-24-56t24-56l229-229Zm0 0 227-227q24-24 56-24t56 24l104 104q24 24 24 56t-24 56L629-373 413-589Z']
const P_Image = ['M200-120q-33 0-56.5-23.5T120-200v-560q0-33 23.5-56.5T200-840h560q33 0 56.5 23.5T840-760v560q0 33-23.5 56.5T760-120H200Zm0-80h560v-560H200v560Zm40-80h480L570-480 450-320l-90-120-120 160Zm-40 80v-560 560Z']
const P_FormatClear = ['m528-546-93-93-121-121h486v120H568l-40 94ZM792-56 460-388l-80 188H249l119-280L56-792l56-56 736 736-56 56Z']
// classic Material Icons "functions" (viewBox 0 0 24 24) — universally
// recognizable fx mark for the math button
const P_Functions = ['M18 4H6v2l6.5 6L6 18v2h12v-3h-7l5-5-5-5h7V4z']
// card-header lifecycle icons (user spec 2026-09-04): add / delete / back
// to preview pool — official Material Symbols outlined paths
const P_Add = ['M440-440H200v-80h240v-240h80v240h240v80H520v240h-80v-240Z']
const P_Delete = ['M280-120q-33 0-56.5-23.5T200-200v-520h-40v-80h200v-40h240v40h200v80h-40v520q0 33-23.5 56.5T680-120H280Zm400-600H280v520h400v-520ZM360-280h80v-360h-80v360Zm160 0h80v-360h-80v360ZM280-720v520-520Z']
const P_RestartAlt = ['M440-122q-121-15-200.5-105.5T160-440q0-66 26-126.5T260-672l57 57q-38 34-57.5 79T240-440q0 88 56 155.5T440-202v80Zm80 0v-80q87-16 143.5-83T720-440q0-100-70-170t-170-70h-3l44 44-56 56-140-140 140-140 56 56-44 44h3q134 0 227 93t93 227q0 121-79.5 211.5T520-122Z']

export interface IconProps { size?: number }

function Svg(props: IconProps & { paths: string[]; viewBox?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      height={props.size ?? 20}
      width={props.size ?? 20}
      viewBox={props.viewBox ?? V}
      fill="currentColor"
    >
      {props.paths.map(p => <path d={p} />)}
    </svg>
  )
}

export const IconFormatBold = (props: IconProps) => <Svg paths={P_FormatBold} {...props} />
export const IconFormatItalic = (props: IconProps) => <Svg paths={P_FormatItalic} {...props} />
export const IconFormatUnderlined = (props: IconProps) => <Svg paths={P_FormatUnderlined} {...props} />
export const IconInkHighlighter = (props: IconProps) => <Svg paths={P_InkHighlighter} {...props} />
export const IconImage = (props: IconProps) => <Svg paths={P_Image} {...props} />
export const IconFormatClear = (props: IconProps) => <Svg paths={P_FormatClear} {...props} />
export const IconFunctions = (props: IconProps) => <Svg paths={P_Functions} viewBox="0 0 24 24" {...props} />
export const IconAdd = (props: IconProps) => <Svg paths={P_Add} {...props} />
export const IconDelete = (props: IconProps) => <Svg paths={P_Delete} {...props} />
export const IconRestartAlt = (props: IconProps) => <Svg paths={P_RestartAlt} {...props} />
