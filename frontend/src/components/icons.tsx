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
// reading mode (渐进制卡, 2026-09-19): menu_book = 阅读/清单入口,
// arrow_upward / vertical_align_top = list priority moves,
// skip_next = 下一张 (keep for later), close = remove from list.
// Official Material Symbols outlined paths (@material-symbols/svg-400).
const P_MenuBook = ['M560-574v-48q33-14 67.5-21t72.5-7q26 0 51 4t49 10v44q-24-9-48.5-13.5T700-610q-38 0-73 9.5T560-574Zm0 220v-49q33-13.5 67.5-20.25T700-430q26 0 51 4t49 10v44q-24-9-48.5-13.5T700-390q-38 0-73 9t-67 27Zm0-110v-48q33-14 67.5-21t72.5-7q26 0 51 4t49 10v44q-24-9-48.5-13.5T700-500q-38 0-73 9.5T560-464ZM248-300q53.57 0 104.28 12.5Q403-275 452-250v-427q-45-30-97.62-46.5Q301.76-740 248-740q-38 0-74.5 9.5T100-707v434q31-14 70.5-20.5T248-300Zm264 50q50-25 98-37.5T712-300q38 0 78.5 6t69.5 16v-429q-34-17-71.82-25-37.82-8-76.18-8-54 0-104.5 16.5T512-677v427Zm-30 90q-51-38-111-58.5T248-239q-36.54 0-71.77 9T106-208q-23.1 11-44.55-3Q40-225 40-251v-463q0-15 7-27.5T68-761q42-20 87.39-29.5 45.4-9.5 92.61-9.5 63 0 122.5 17T482-731q51-35 109.5-52T712-800q46.87 0 91.93 9.5Q849-781 891-761q14 7 21.5 19.5T920-714v463q0 27.89-22.5 42.45Q875-194 853-208q-34-14-69.23-22.5Q748.54-239 712-239q-63 0-121 21t-109 58ZM276-489Z']
const P_ArrowUpward = ['M450-160v-526L202-438l-42-42 320-320 320 320-42 42-248-248v526h-60Z']
const P_VerticalAlignTop = ['M160-780v-60h640v60H160Zm290 660v-484L329-483l-43-43 194-194 190 190-43 43-117-117v484h-60Z']
const P_SkipNext = ['M680-240v-480h60v480h-60Zm-460 0v-480l346 240-346 240Zm60-240Zm0 125 181-125-181-125v250Z']
const P_Close = ['m249-207-42-42 231-231-231-231 42-42 231 231 231-231 42 42-231 231 231 231-42 42-231-231-231 231Z']
// 挖空卡 (user spec 2026-09-19): password = the classic "redacted/cloze"
// mark; folder / description / chevron_right = tree file-picker icons.
const P_Password = ['M80-200v-61h800v61H80Zm38-254-40-22 40-68H40v-45h78l-40-68 40-22 38 67 38-67 40 22-40 68h78v45h-78l40 68-40 22-38-67-38 67Zm324 0-40-24 40-68h-78v-45h78l-40-68 40-22 38 67 38-67 40 22-40 68h78v45h-78l40 68-40 24-38-67-38 67Zm324 0-40-24 40-68h-78v-45h78l-40-68 40-22 38 67 38-67 40 22-40 68h78v45h-78l40 68-40 24-38-67-38 67Z']
const P_Folder = ['M140-160q-24 0-42-18.5T80-220v-520q0-23 18-41.5t42-18.5h281l60 60h339q23 0 41.5 18.5T880-680v460q0 23-18.5 41.5T820-160H140Zm0-60h680v-460H456l-60-60H140v520Zm0 0v-520 520Z']
// nav rail (user spec 2026-09-19 round 3): school = 学习 destination
// (official Material Symbols outlined, @material-symbols/svg-400)
const P_School = ['M479-120 189-279v-240L40-600l439-240 441 240v317h-60v-282l-91 46v240L479-120Zm0-308 315-172-315-169-313 169 313 172Zm0 240 230-127v-168L479-360 249-485v170l230 127Zm1-240Zm-1 74Zm0 0Z']
const P_Description = ['M319-250h322v-60H319v60Zm0-170h322v-60H319v60ZM220-80q-24 0-42-18t-18-42v-680q0-24 18-42t42-18h361l219 219v521q0 24-18 42t-42 18H220Zm331-554v-186H220v680h520v-494H551ZM220-820v186-186 680-680Z']
const P_ChevronRight = ['M530-481 332-679l43-43 241 241-241 241-43-43 198-198Z']
// 三栏重构 (user spec 2026-09-21): content_cut = 分割文段 (split a chunk
// into smaller reading cards), find_in_page = 溯源 (jump from a review card
// back to its source segment), arrow_back = 回到复习 (return from the
// traced reading detour). Official Material Symbols outlined paths
// (@material-symbols/svg-400 0.47.4).
const P_ContentCut = ['M782-114 481-415 364-298q11 17 13.5 33t2.5 35q0 64-43 107T230-80q-64 0-107-43T80-230q0-64 43-107t107-43q18 0 35.5 5t36.5 15l116-116-118-118q-17 8-34.5 11t-35.5 3q-64 0-107-43T80-730q0-64 43-107t107-43q64 0 107 43t43 107q0 19-2.5 36T367-662l514 514v34h-99ZM599-527l-66-66 249-249h99v33L599-527ZM294-666q26-26 26-64t-26-64q-26-26-64-26t-64 26q-26 26-26 64t26 64q26 26 64 26t64-26Zm202.5 203.5Q502-468 502-476t-5.5-13.5Q491-495 483-495t-13.5 5.5Q464-484 464-476t5.5 13.5Q475-457 483-457t13.5-5.5ZM294-166q26-26 26-64t-26-64q-26-26-64-26t-64 26q-26 26-26 64t26 64q26 26 64 26t64-26Z']
const P_FindInPage = ['m652-140 60 60H220q-24 0-42-18t-18-42v-680q0-24 18-42t42-18h372l208 239v487q0 14-7.5 28.5T774-102L562-314q-20 12-39.68 17-19.69 5-42.32 5-62 0-104.5-42.5T333-439q0-62 42.5-104.5T480-586q62 0 104.5 42.5T627-439q0 23-6.5 44T601-358l139 139v-403L568-820H220v680h432ZM542.5-381.5Q567-411 567-450q0-33-26-54.5T480-526q-35 0-61 21.5T393-450q0 39 24.5 68.5T480-352q38 0 62.5-29.5ZM480-450Zm0 0Z']
const P_ArrowBack = ['m274-450 248 248-42 42-320-320 320-320 42 42-248 248h526v60H274Z']

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
export const IconMenuBook = (props: IconProps) => <Svg paths={P_MenuBook} {...props} />
export const IconArrowUpward = (props: IconProps) => <Svg paths={P_ArrowUpward} {...props} />
export const IconVerticalAlignTop = (props: IconProps) => <Svg paths={P_VerticalAlignTop} {...props} />
export const IconSkipNext = (props: IconProps) => <Svg paths={P_SkipNext} {...props} />
export const IconClose = (props: IconProps) => <Svg paths={P_Close} {...props} />
export const IconPassword = (props: IconProps) => <Svg paths={P_Password} {...props} />
export const IconFolder = (props: IconProps) => <Svg paths={P_Folder} {...props} />
export const IconDescription = (props: IconProps) => <Svg paths={P_Description} {...props} />
export const IconChevronRight = (props: IconProps) => <Svg paths={P_ChevronRight} {...props} />
export const IconSchool = (props: IconProps) => <Svg paths={P_School} {...props} />
export const IconContentCut = (props: IconProps) => <Svg paths={P_ContentCut} {...props} />
export const IconFindInPage = (props: IconProps) => <Svg paths={P_FindInPage} {...props} />
export const IconArrowBack = (props: IconProps) => <Svg paths={P_ArrowBack} {...props} />
