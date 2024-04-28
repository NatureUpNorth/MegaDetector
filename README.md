Hello,

This repository contains an edited version of the megadetector image recognition system. The run_detector file is used to sort images based on whether or not they contain an animal. It uses a confidence level that you can specify to sort the images. Copies of images at or above the level will be sorted into a folder that you create on your machine. All original images will remain in the original folder that you are using. Make sure you have an updated version of megadetector downloaded on your machine. The download can be found here: https://github.com/agentmorris/MegaDetector/blob/main/megadetector.md#downloading-the-model.


Here is an example for setting up the model:


```
if __name__ == '__main__':
#    main()
    #%%
    # specify the path to megadetector file on your machine
    model_file = "C:\\Users\\bkara\\Downloads\\md_v5a.0.0.pt"
    # specify path to name of your image folder
    image_file_names = path_utils.find_images("C:\\Users\\bkara\\OneDrive - St. Lawrence University\\temp")
    print(image_file_names)
    # specifying paths to output folders on your machine based off of threshold
    output_dir = "C:\\Users\\bkara\\OneDrive - St. Lawrence University\\temp"
    true_output_dir = "C:\\Users\\bkara\\Downloads\\true"
    false_output_dir = "C:\\Users\\bkara\\Downloads\\false"
    # create confidence threshold to find images containing animals
    render_confidence_threshold = 0.8
    crop_images = True
```
This chunk can be found at around line 690 of the run_detector file.

Good Luck!
