/* SPDX-FileCopyrightText: 2017 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup spsidebar
 */

#include <cstring>

#include "MEM_guardedalloc.h"

#include "BLI_listbase.h"
#include "BLI_string.h"
#include "BLI_string_search.hh"
#include "BLI_utildefines.h"

#include "BLT_translation.hh"

#include "BKE_context.hh"
#include "BKE_report.hh"
#include "BKE_screen.hh"
#include "BKE_undo_system.hh"
#include "ED_screen.hh"
#include "ED_space_api.hh"

#include "UI_interface.hh"
#include "UI_resources.hh"
#include "UI_view2d.hh"

#include "BLO_read_write.hh"

#include "RNA_access.hh"
#include "RNA_define.hh"
#include "RNA_enum_types.hh"

#include "WM_api.hh"
#include "WM_message.hh"
#include "WM_types.hh"

#include "BKE_blender_undo.hh"
/* ******************** default callbacks for sidebar space ***************** */

static SpaceLink *sidebar_create(const ScrArea * /*area*/, const Scene * /*scene*/)
{
  ARegion *region;
  SpaceSideBar *ssidebar;

  ssidebar = static_cast<SpaceSideBar *>(MEM_callocN(sizeof(*ssidebar), "init sidebar"));
  ssidebar->spacetype = SPACE_SIDEBAR;

  /* header */
  region = BKE_area_region_new();
  BLI_addtail(&ssidebar->regionbase, region);
  region->regiontype = RGN_TYPE_HEADER;
  region->alignment = RGN_ALIGN_TOP;

  /* main regions */
  region = BKE_area_region_new();
  BLI_addtail(&ssidebar->regionbase, region);
  region->regiontype = RGN_TYPE_WINDOW;

  /* footer - new region type */
  region = BKE_area_region_new();
  BLI_addtail(&ssidebar->regionbase, region);
  region->regiontype = RGN_TYPE_FOOTER;
  region->alignment = RGN_ALIGN_BOTTOM;

  return (SpaceLink *)ssidebar;
}

/* Doesn't free the space-link itself. */
static void sidebar_free(SpaceLink * /*sl*/) {}

/* spacetype; init callback */
static void sidebar_init(wmWindowManager * /*wm*/, ScrArea * /*area*/) {}

static SpaceLink *sidebar_duplicate(SpaceLink *sl)
{
  SpaceSideBar *ssidebarn = static_cast<SpaceSideBar *>(MEM_dupallocN(sl));

  /* clear or remove stuff from old */

  return (SpaceLink *)ssidebarn;
}

/* add handlers, stuff you only do once or on area/region changes */
static void sidebar_main_region_init(wmWindowManager *wm, ARegion *region)
{
  wmKeyMap *keymap;
  // Set this flag to indicate content should be scrollable
  region->v2d.flag |= V2D_VIEWSYNC_SCREEN_TIME;
  region->v2d.scroll = (V2D_SCROLL_RIGHT | V2D_SCROLL_BOTTOM);

  /* force delayed UI_view2d_region_reinit call */
  if (ELEM(RGN_ALIGN_ENUM_FROM_MASK(region->alignment), RGN_ALIGN_RIGHT)) {
    region->flag |= RGN_FLAG_DYNAMIC_SIZE;
  }
  UI_view2d_region_reinit(&region->v2d, V2D_COMMONVIEW_LIST, region->winx, region->winy);

  keymap = WM_keymap_ensure(wm->defaultconf, "View2D Buttons List", SPACE_EMPTY, RGN_TYPE_WINDOW);
  WM_event_add_keymap_handler(&region->runtime->handlers, keymap);
}

static void sidebar_keymap(wmKeyConfig * /*keyconf*/) {}

/* add handlers, stuff you only do once or on area/region changes */
static void sidebar_header_region_init(wmWindowManager * /*wm*/, ARegion *region)
{
  // Enable dynamic sizing for all sidebar regions
  // region->flag |= RGN_FLAG_DYNAMIC_SIZE;

  ED_region_header_init(region);
}

static void sidebar_main_region_listener(const wmRegionListenerParams *params)
{
  ARegion *region = params->region;
  const wmNotifier *wmn = params->notifier;

  /* context changes */
  switch (wmn->category) {
    case NC_WM:
      if (wmn->data == ND_HISTORY) {
        ED_region_tag_redraw(region);
      }
      break;
    case NC_SCENE:
      if (wmn->data == ND_MODE) {
        ED_region_tag_redraw(region);
      }
      break;
    case NC_SPACE:
      if (wmn->data == ND_SPACE_VIEW3D) {
        ED_region_tag_redraw(region);
      }
      break;
    case NC_GPENCIL:
      if (wmn->data == ND_DATA) {
        ED_region_tag_redraw(region);
      }
      break;
  }
}

static void sidebar_header_listener(const wmRegionListenerParams *params)
{
  ARegion *region = params->region;
  const wmNotifier *wmn = params->notifier;

  /* context changes */
  switch (wmn->category) {
    case NC_WM:
      if (wmn->data == ND_JOB) {
        ED_region_tag_redraw(region);
      }
      break;
    case NC_WORKSPACE:
      ED_region_tag_redraw(region);
      break;
    case NC_SPACE:
      if (wmn->data == ND_SPACE_INFO) {
        ED_region_tag_redraw(region);
      }
      break;
    case NC_SCREEN:
      if (wmn->data == ND_LAYER) {
        ED_region_tag_redraw(region);
      }
      break;
    case NC_SCENE:
      if (wmn->data == ND_SCENEBROWSE) {
        ED_region_tag_redraw(region);
      }
      break;
  }
}

static int sidebar_toggle_exec(bContext *C, wmOperator *op)
{
  UNUSED_VARS(op);

  // Toggle the sidebar visibility
  bScreen *screen = CTX_wm_screen(C);
  ED_sidebar_toggle_visibility(screen);

  // Force screen refresh
  WM_event_add_notifier(C, NC_WINDOW, NULL);

  return OPERATOR_FINISHED;
}

static void SIDEBAR_OT_toggle(wmOperatorType *ot)
{
  /* identifiers */
  ot->name = "Toggle Sidebar";
  ot->description = "Toggle the visibility of the global sidebar";
  ot->idname = "SIDEBAR_OT_toggle";

  /* api callbacks */
  ot->exec = sidebar_toggle_exec;

  /* flags - make sure this includes REGISTER to expose to Python */
  ot->flag = OPTYPE_REGISTER | OPTYPE_UNDO;
}

/* New operator to force refresh the sidebar */
static int sidebar_refresh_exec(bContext *C, wmOperator *op)
{
  UNUSED_VARS(op);

  wmWindowManager *wm = CTX_wm_manager(C);
  wmWindow *win = CTX_wm_window(C);

  if (!win) {
    LISTBASE_FOREACH (wmWindow *, iter_win, &wm->windows) {
      win = iter_win;
      break;
    }
  }

  if (win && win->screen) {  // Just use win->screen directly
    LISTBASE_FOREACH (ScrArea *, area, &win->screen->areabase) {
      if (area->spacetype == SPACE_SIDEBAR) {
        LISTBASE_FOREACH (ARegion *, region, &area->regionbase) {
          ED_region_tag_redraw(region);
        }
      }
    }
  }

  WM_event_add_notifier(C, NC_WINDOW, NULL);

  return OPERATOR_FINISHED;
}

static void SIDEBAR_OT_refresh(wmOperatorType *ot)
{
  /* identifiers */
  ot->name = "Refresh Sidebar";
  ot->description = "Force a redraw of the global sidebar";
  ot->idname = "SIDEBAR_OT_refresh"; /* Changed idname */
  /* api callbacks */
  ot->exec = sidebar_refresh_exec;

  /* flags - make sure this includes REGISTER to expose to Python */
  /* No UNDO needed for a refresh action */
  ot->flag = OPTYPE_REGISTER;
}

static int sidebar_checkpoint_undo_exec(bContext *C, wmOperator *op)
{
  wmWindowManager *wm = CTX_wm_manager(C);
  if (!wm->undo_stack) {
    printf("DEBUG: No undo stack available\n");
    BKE_report(op->reports, RPT_ERROR, "No undo history available");
    return OPERATOR_CANCELLED;
  }

  printf("DEBUG: Starting checkpoint search in undo stack\n");

  /* Retrieve optional checkpoint name parameter */
  char checkpoint_name_buf[BKE_UNDO_STR_MAX];
  RNA_string_get(op->ptr, "checkpoint_name", checkpoint_name_buf);
  const char *checkpoint_name = checkpoint_name_buf;
  const bool search_by_name = (checkpoint_name[0] != '\0');

  /* Count total steps first for correct indexing */
  int total_steps = 0;
  LISTBASE_FOREACH (UndoStep *, us_count, &wm->undo_stack->steps) {
    if (!us_count->skip) {
      total_steps++;
    }
  }
  printf("DEBUG: Total valid undo steps: %d\n", total_steps);

  /* Find the target checkpoint */
  int target_checkpoint_index = -1;
  const char *target_checkpoint_id =
      checkpoint_name;                  // Use the passed name directly as the ID to find
  int current_index = total_steps - 1;  // Start from the most recent valid step index

  // We only search by specific ID now
  if (target_checkpoint_id[0] == '\0') {
    printf("DEBUG: No checkpoint ID provided to search for.\n");
    BKE_report(op->reports, RPT_ERROR, "No checkpoint ID provided for undo.");
    return OPERATOR_CANCELLED;
  }
  printf("DEBUG: Searching for undo step with exact name/ID: '%s'\n", target_checkpoint_id);

  LISTBASE_FOREACH_BACKWARD (UndoStep *, us, &wm->undo_stack->steps) {
    // Check if name pointer is valid and string is not empty
    if (us->skip || !us->name || us->name[0] == '\0') {  // More robust check
      // Skip invalid steps, but don't decrement current_index if it was skipped
      if (!us->skip) {
        current_index--;  // Decrement only if it was a valid step we're passing over
      }
      continue;
    }

    // DEBUG: Log the step being checked
    // printf("DEBUG: Checking step %d: '%s' (active: %s)\n",
    //        current_index,
    //        us->name,
    //        (us == wm->undo_stack->step_active) ? "yes" : "no");

    // Search for exact name match (which is the checkpoint ID)
    if (strcmp(us->name, target_checkpoint_id) == 0) {
      target_checkpoint_index = current_index;
      printf("DEBUG: Found matching undo step by ID at index %d: '%s'\n",
             target_checkpoint_index,
             us->name);
      break;  // Found the specific checkpoint ID
    }

    current_index--;  // Decrement index after checking a valid step
  }

  if (target_checkpoint_index != -1) {
    printf("DEBUG: Attempting to undo to checkpoint at index %d\n", target_checkpoint_index);
    /* Create and call undo_history operator */
    PointerRNA ptr;
    WM_operator_properties_create(&ptr, "ED_OT_undo_history");
    RNA_int_set(&ptr, "item", target_checkpoint_index);

    // Log the actual value being set
    int set_value = RNA_int_get(&ptr, "item");
    printf("DEBUG: Set undo history item value to: %d\n", set_value);

    WM_operator_name_call(C, "ED_OT_undo_history", WM_OP_EXEC_DEFAULT, &ptr, nullptr);
    WM_operator_properties_free(&ptr);

    printf("DEBUG: Undo history operator called\n");
    return OPERATOR_FINISHED;
  }

  // If loop finished without finding the ID
  printf("DEBUG: No undo step found matching ID '%s'\n", target_checkpoint_id);
  BKE_reportf(
      op->reports, RPT_WARNING, "No undo step found matching ID '%s'", target_checkpoint_id);

  return OPERATOR_CANCELLED;
}

static void SIDEBAR_OT_checkpoint_undo(wmOperatorType *ot)
{
  /* identifiers */
  ot->name = "Undo to Checkpoint";
  ot->description = "Rewind history to a specific checkpoint ID";  // Updated description
  ot->idname = "SIDEBAR_OT_checkpoint_undo";

  /* api callbacks */
  ot->exec = sidebar_checkpoint_undo_exec;

  /* properties */
  // Correctly define the property using ot->prop
  ot->prop = RNA_def_string(
      ot->srna,
      "checkpoint_name",  // Keep name, but it now holds the ID
      nullptr,
      BKE_UNDO_STR_MAX,                                                    // Use BKE_UNDO_STR_MAX
      "Checkpoint ID",                                                     // Updated UI label
      "The specific LangGraph checkpoint ID to revert Blender state to");  // Updated description

  /* flags */
  /* Don't add OPTYPE_REGISTER or OPTYPE_UNDO here. we dont want it to show up in the undo
   * history*/
}

static void sidebar_operatortypes()
{
  WM_operatortype_append(SIDEBAR_OT_toggle);
  WM_operatortype_append(SIDEBAR_OT_refresh);         /* Register the new operator */
  WM_operatortype_append(SIDEBAR_OT_checkpoint_undo); /* Register checkpoint undo operator */
}

static void sidebar_header_region_message_subscribe(const wmRegionMessageSubscribeParams *params)
{
  wmMsgBus *mbus = params->message_bus;
  WorkSpace *workspace = params->workspace;
  ARegion *region = params->region;

  wmMsgSubscribeValue msg_sub_value_region_tag_redraw{};
  msg_sub_value_region_tag_redraw.owner = region;
  msg_sub_value_region_tag_redraw.user_data = region;
  msg_sub_value_region_tag_redraw.notify = ED_region_do_msg_notify_tag_redraw;

  WM_msg_subscribe_rna_prop(
      mbus, &workspace->id, workspace, WorkSpace, tools, &msg_sub_value_region_tag_redraw);
}

static void recent_files_menu_draw(const bContext * /*C*/, Menu *menu)
{
  uiLayout *layout = menu->layout;
  uiLayoutSetOperatorContext(layout, WM_OP_INVOKE_DEFAULT);
  if (uiTemplateRecentFiles(layout, U.recent_files) != 0) {
    uiItemS(layout);
    uiItemO(layout, IFACE_("Clear Recent Files List..."), ICON_TRASH, "WM_OT_clear_recent_files");
  }
  else {
    uiItemL(layout, IFACE_("No Recent Files"), ICON_NONE);
  }
}

static void recent_files_menu_register()
{
  MenuType *mt;

  mt = static_cast<MenuType *>(MEM_callocN(sizeof(MenuType), "spacetype info menu recent files"));
  STRNCPY(mt->idname, "SIDEBAR_MT_file_open_recent");
  STRNCPY(mt->label, N_("Open Recent"));
  STRNCPY(mt->translation_context, BLT_I18NCONTEXT_DEFAULT_BPYRNA);
  mt->draw = recent_files_menu_draw;
  WM_menutype_add(mt);
}

static void undo_history_draw_menu(const bContext *C, Menu *menu)
{
  wmWindowManager *wm = CTX_wm_manager(C);
  if (wm->undo_stack == nullptr) {
    return;
  }

  int undo_step_count = 0;
  int undo_step_count_all = 0;
  LISTBASE_FOREACH_BACKWARD (UndoStep *, us, &wm->undo_stack->steps) {
    undo_step_count_all += 1;
    if (us->skip) {
      continue;
    }
    undo_step_count += 1;
  }

  uiLayout *split = uiLayoutSplit(menu->layout, 0.0f, false);
  uiLayout *column = nullptr;

  const int col_size = 20 + (undo_step_count / 12);

  undo_step_count = 0;

  /* Reverse the order so the most recent state is first in the menu. */
  int i = undo_step_count_all - 1;
  for (UndoStep *us = static_cast<UndoStep *>(wm->undo_stack->steps.last); us; us = us->prev, i--)
  {
    if (us->skip) {
      continue;
    }
    if (!(undo_step_count % col_size)) {
      column = uiLayoutColumn(split, false);
    }
    const bool is_active = (us == wm->undo_stack->step_active);
    uiLayout *row = uiLayoutRow(column, false);
    uiLayoutSetEnabled(row, !is_active);
    uiItemIntO(row,
               CTX_IFACE_(BLT_I18NCONTEXT_OPERATOR_DEFAULT, us->name),
               is_active ? ICON_LAYER_ACTIVE : ICON_NONE,
               "ED_OT_undo_history",
               "item",
               i);
    undo_step_count += 1;
  }
}

static void undo_history_menu_register()
{
  MenuType *mt;

  mt = static_cast<MenuType *>(MEM_callocN(sizeof(MenuType), __func__));
  STRNCPY(mt->idname, "SIDEBAR_MT_undo_history");
  STRNCPY(mt->label, N_("Undo History"));
  STRNCPY(mt->translation_context, BLT_I18NCONTEXT_DEFAULT_BPYRNA);
  mt->draw = undo_history_draw_menu;
  WM_menutype_add(mt);
}

static void checkpoint_undo_draw_menu(const bContext *C, Menu *menu)
{
  wmWindowManager *wm = CTX_wm_manager(C);
  if (wm->undo_stack == nullptr) {
    uiItemL(menu->layout, IFACE_("No Undo History"), ICON_NONE);
    return;
  }

  int undo_step_count_all = 0;
  LISTBASE_FOREACH_BACKWARD (UndoStep *, us_counter, &wm->undo_stack->steps) {
    undo_step_count_all += 1;
  }

  int latest_checkpoint_index = -1;
  const char *latest_checkpoint_name = nullptr;
  const char *checkpoint_prefix = "Checkpoint:";
  const size_t checkpoint_prefix_len = strlen(checkpoint_prefix);

  /* Iterate backwards to find the most recent checkpoint */
  int i = undo_step_count_all - 1;
  for (UndoStep *us = static_cast<UndoStep *>(wm->undo_stack->steps.last); us; us = us->prev, i--)
  {
    if (us->skip) {
      continue;
    }
    /* Check if the name starts with "Checkpoint:" */
    if (us->name && us->name[0] != '\0' &&
        strncmp(us->name, checkpoint_prefix, checkpoint_prefix_len) == 0)
    {
      latest_checkpoint_index = i;
      latest_checkpoint_name = us->name;
      break; /* Found the most recent one */
    }
  }

  /* Draw the button or a label */
  if (latest_checkpoint_index != -1 && latest_checkpoint_name && latest_checkpoint_name[0] != '\0')
  {
    /* Use uiItemIntO to create a button that calls ED_OT_undo_history with the specific index */
    uiItemIntO(menu->layout,
               CTX_IFACE_(BLT_I18NCONTEXT_OPERATOR_DEFAULT, latest_checkpoint_name),
               ICON_NONE,
               "ED_OT_undo_history",
               "item",
               latest_checkpoint_index);
  }
  else {
    uiItemL(menu->layout, IFACE_("No Checkpoint Found"), ICON_NONE);
    uiLayoutSetEnabled(menu->layout, false); /* Disable the menu if no checkpoint */
  }
}

static void checkpoint_undo_menu_register()
{
  MenuType *mt;

  mt = static_cast<MenuType *>(MEM_callocN(sizeof(MenuType), __func__));
  STRNCPY(mt->idname, "SIDEBAR_MT_checkpoint_undo");
  STRNCPY(mt->label, N_("Go To Checkpoint")); /* Default label */
  STRNCPY(mt->translation_context, BLT_I18NCONTEXT_DEFAULT_BPYRNA);
  mt->draw = checkpoint_undo_draw_menu;
  WM_menutype_add(mt);
}

static void sidebar_space_blend_write(BlendWriter *writer, SpaceLink *sl)
{
  BLO_write_struct(writer, SpaceSideBar, sl);
}

static void sidebar_main_region_draw(const bContext *C, ARegion *region)
{
  ED_region_panels_layout(C, region);
  ED_region_panels_draw(C, region);
}

static void sidebar_header_region_draw(const bContext *C, ARegion *region)
{
  ED_region_header_layout(C, region);
  ED_region_header_draw(C, region);
}

/* add handlers, stuff you only do once or on area/region changes */
static void sidebar_footer_region_init(wmWindowManager * /*wm*/, ARegion *region)
{
  // Enable dynamic sizing for footer region if needed
  // region->flag |= RGN_FLAG_DYNAMIC_SIZE;

  ED_region_header_init(region);
}

static void sidebar_footer_listener(const wmRegionListenerParams *params)
{
  ARegion *region = params->region;
  const wmNotifier *wmn = params->notifier;

  /* context changes */
  switch (wmn->category) {
    case NC_WM:
      if (wmn->data == ND_JOB) {
        ED_region_tag_redraw(region);
      }
      break;
    case NC_WORKSPACE:
      ED_region_tag_redraw(region);
      break;
    case NC_SPACE:
      if (wmn->data == ND_SPACE_INFO) {
        ED_region_tag_redraw(region);
      }
      break;
    case NC_SCREEN:
      if (wmn->data == ND_LAYER) {
        ED_region_tag_redraw(region);
      }
      break;
    case NC_SCENE:
      if (wmn->data == ND_SCENEBROWSE) {
        ED_region_tag_redraw(region);
      }
      break;
  }
}

static void sidebar_footer_region_draw(const bContext *C, ARegion *region)
{
  ED_region_header_layout(C, region);
  ED_region_header_draw(C, region);
}

void ED_spacetype_sidebar()
{
  std::unique_ptr<SpaceType> st = std::make_unique<SpaceType>();
  ARegionType *art;

  st->spaceid = SPACE_SIDEBAR;
  STRNCPY(st->name, "Side Bar");

  st->create = sidebar_create;
  st->free = sidebar_free;
  st->init = sidebar_init;
  st->duplicate = sidebar_duplicate;
  st->operatortypes = sidebar_operatortypes;
  st->keymap = sidebar_keymap;
  st->blend_write = sidebar_space_blend_write;

  /* regions: main window */
  art = static_cast<ARegionType *>(
      MEM_callocN(sizeof(ARegionType), "spacetype sidebar main region"));
  art->regionid = RGN_TYPE_WINDOW;
  art->init = sidebar_main_region_init;
  art->layout = ED_region_panels_layout;
  art->draw = sidebar_main_region_draw;
  art->listener = sidebar_main_region_listener;
  art->prefsizex = UI_UNIT_X * 20;
  art->keymapflag = ED_KEYMAP_UI | ED_KEYMAP_VIEW2D;

  BLI_addhead(&st->regiontypes, art);

  /* regions: header */
  art = static_cast<ARegionType *>(
      MEM_callocN(sizeof(ARegionType), "spacetype sidebar header region"));
  art->regionid = RGN_TYPE_HEADER;
  art->prefsizex = UI_UNIT_X * 20;
  art->prefsizey = UI_UNIT_Y * 5;
  art->keymapflag = ED_KEYMAP_UI | ED_KEYMAP_VIEW2D | ED_KEYMAP_HEADER;
  art->listener = sidebar_header_listener;
  art->message_subscribe = sidebar_header_region_message_subscribe;
  art->init = sidebar_header_region_init;
  art->layout = sidebar_header_region_draw;
  art->draw = sidebar_header_region_draw;

  BLI_addhead(&st->regiontypes, art);

  /* regions: footer - new region type */
  art = static_cast<ARegionType *>(
      MEM_callocN(sizeof(ARegionType), "spacetype sidebar footer region"));
  art->regionid = RGN_TYPE_FOOTER;
  art->prefsizex = UI_UNIT_X * 20;
  art->prefsizey = UI_UNIT_Y * 6;  // Make it slightly taller than the header
  art->keymapflag = ED_KEYMAP_UI | ED_KEYMAP_VIEW2D | ED_KEYMAP_FOOTER;
  art->listener = sidebar_footer_listener;
  art->message_subscribe =
      sidebar_header_region_message_subscribe;  // Reuse the same subscription handler
  art->init = sidebar_footer_region_init;
  art->layout = sidebar_footer_region_draw;
  art->draw = sidebar_footer_region_draw;

  BLI_addhead(&st->regiontypes, art);

  recent_files_menu_register();
  undo_history_menu_register();
  checkpoint_undo_menu_register();

  BKE_spacetype_register(std::move(st));
}
