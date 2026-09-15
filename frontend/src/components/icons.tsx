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
// MD3 audit (2026-09-16): official Material Symbols outlined paths for the
// pacing-mode tiles (bolt = 快速, track_changes = 专注 — a literal target) and
// the selected-tile indicator (check_circle). Fetched verbatim from the
// @material-symbols/svg-400 package, replacing the former emoji icons.
const P_Bolt = ['m393-165 279-335H492l36-286-253 366h154l-36 255Zm-73 85 40-280H160l360-520h80l-40 320h240L400-80h-80Zm154-396Z']
const P_TrackChanges = ['M324-111.5Q251-143 197-197t-85.5-127Q80-397 80-480t31.5-156Q143-709 197-763t127-85.5Q397-880 480-880h30v326q22 9 36 29t14 45q0 33-23.5 56.5T480-400q-33 0-56.5-23.5T400-480q0-25 14-45t36-29v-104q-65 11-107.5 60.5T300-480q0 75 52.5 127.5T480-300q75 0 127.5-52.5T660-480q0-41-16-75t-44-59l43-43q35 33 56 78.5t21 98.5q0 100-70 170t-170 70q-100 0-170-70t-70-170q0-93 60-160.5T450-719v-100q-131 11-220.5 108T140-480q0 142 99 241t241 99q142 0 241-99t99-241q0-74-28.5-137T713-727l43-43q57 55 90.5 129.5T880-480q0 83-31.5 156T763-197q-54 54-127 85.5T480-80q-83 0-156-31.5Z']
const P_CheckCircle = ['m421-298 283-283-46-45-237 237-120-120-45 45 165 166Zm59 218q-82 0-155-31.5t-127.5-86Q143-252 111.5-325T80-480q0-83 31.5-156t86-127Q252-817 325-848.5T480-880q83 0 156 31.5T763-763q54 54 85.5 127T880-480q0 82-31.5 155T763-197.5q-54 54.5-127 86T480-80Zm0-60q142 0 241-99.5T820-480q0-142-99-241t-241-99q-141 0-240.5 99T140-480q0 141 99.5 240.5T480-140Zm0-340Z']
// undo / edit — official Material Symbols outlined, replacing the legacy
// hand-drawn 24px Material Icons paths (MD3 audit 2026-09-16)
const P_Undo = ['M259-200v-60h310q70 0 120.5-46.5T740-422q0-69-50.5-115.5T569-584H274l114 114-42 42-186-186 186-186 42 42-114 114h294q95 0 163.5 64T800-422q0 94-68.5 158T568-200H259Z']
const P_Edit = ['M180-180h44l472-471-44-44-472 471v44Zm-60 60v-128l575-574q8-8 19-12.5t23-4.5q11 0 22 4.5t20 12.5l44 44q9 9 13 20t4 22q0 11-4.5 22.5T823-694L248-120H120Zm659-617-41-41 41 41Zm-105 64-22-22 44 44-22-22Z']

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
export const IconBolt = (props: IconProps) => <Svg paths={P_Bolt} {...props} />
export const IconTrackChanges = (props: IconProps) => <Svg paths={P_TrackChanges} {...props} />
export const IconCheckCircle = (props: IconProps) => <Svg paths={P_CheckCircle} {...props} />
export const IconUndo = (props: IconProps) => <Svg paths={P_Undo} {...props} />
export const IconEdit = (props: IconProps) => <Svg paths={P_Edit} {...props} />
