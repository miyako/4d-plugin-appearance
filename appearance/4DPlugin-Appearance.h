/* --------------------------------------------------------------------------------
 #
 #	4DPlugin-Appearance.h
 #	source generated by 4D Plugin Wizard
 #	Project : Appearance
 #	author : miyako
 #	2022/04/29
 #  
 # --------------------------------------------------------------------------------*/

#ifndef PLUGIN_APPEARANCE_H
#define PLUGIN_APPEARANCE_H

#include "4DPluginAPI.h"

#import <CoreServices/CoreServices.h>
#import <Foundation/Foundation.h>
#import <Cocoa/Cocoa.h>

#include <string>
#include <vector>

#include <mutex>
#include <vector>
#include <iostream>

static void listenerInit(void);
static void listenerLoop(void);
static void listenerLoopStart(void);
static void listenerLoopFinish(void);
static void listenerLoopExecute(void);
static void listenerLoopExecuteMethod(void);

typedef PA_long32 process_number_t;
typedef PA_long32 process_stack_size_t;
typedef PA_long32 method_id_t;
typedef PA_Unichar* process_name_t;

typedef enum {
    
    appearance_light = 0,
    appearance_dark  = 1,
    appearance_auto  = 2
}appearance_t;

#pragma mark -

static void Get_system_color_scheme(PA_PluginParameters params);
static void ON_APPEARANCE_CHANGE_CALL(PA_PluginParameters params);

#endif /* PLUGIN_APPEARANCE_H */